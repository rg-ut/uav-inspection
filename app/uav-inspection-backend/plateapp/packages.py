
"""Validate and atomically activate immutable plate replay data packages."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
from fastapi import HTTPException

from authapp.api import require_account
from deployment_paths import backend_path

CATEGORY = 'plate-data-package'
ROOT = backend_path('platedata-versions')
MAX_UPLOAD = 512 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024
ID = re.compile(r'[0-9a-f]{32}')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
JSON_FILES = {'timeline.json', 'coverage.json', 'acceptance.json', 'media/path.json'}
MEDIA_TYPES = {'.mp4', '.jpg', '.jpeg', '.png'}
ARCHIVE_SUFFIXES = ('.zip', '.tar.gz', '.tgz')


def can_publish(account):
    names = os.getenv('UAV_PLATE_DATA_UPLOADERS', '').split(',')
    return account['username'].casefold() in {name.strip().casefold() for name in names if name.strip()}


def require_publisher(request):
    account = require_account(request)
    if not can_publish(account):
        raise HTTPException(403, '当前账号没有车牌数据发布权限。')
    return account


def _db():
    return sqlite3.connect(ROOT / 'activation.sqlite3', timeout=15)


def _active(connection):
    return connection.execute('SELECT current_id, previous_id FROM activation WHERE id = 1').fetchone()


def selection():
    if not (ROOT / 'activation.sqlite3').is_file():
        return None
    with closing(_db()) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'activation'").fetchone():
            return None
        return _active(connection)


def current_revision():
    row = selection()
    return row[0] if row else None


def revision_root(revision: str):
    if not ID.fullmatch(revision):
        raise HTTPException(404, '未找到素材版本。')
    return ROOT / revision


def status(revision):
    if revision is None:
        return None
    return json.loads((revision_root(revision) / 'manifest.json').read_text(encoding='utf-8'))


def restore():
    if selection() is None:
        raise HTTPException(409, '还没有上一批可恢复。')
    with closing(_db()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        row = _active(connection)
        if row is None:
            raise HTTPException(409, '还没有上一批可恢复。')
        if row[1] is not None:
            status(row[1])
        connection.execute('UPDATE activation SET current_id = ?, previous_id = ? WHERE id = 1', (row[1], row[0]))


def _member_name(source):
    name = source.removeprefix('./').rstrip('/')
    parts = name.split('/')
    if not parts or parts[0] != 'platedata' or not all(NAME.fullmatch(part) and part not in ('.', '..') for part in parts):
        raise ValueError('数据包包含不支持的文件路径。')
    return '/'.join(parts[1:])


def _unpack(source, directory):
    total = 0
    seen = set()
    with tarfile.open(fileobj=source, mode='r|gz') as archive:
        for index, member in enumerate(archive):
            if index >= 512:
                raise ValueError('数据包文件数超过 512 个。')
            name = _member_name(member.name)
            if name.casefold() in seen:
                raise ValueError('数据包包含重复文件。')
            seen.add(name.casefold())
            if member.isdir() and name in ('', 'media', 'cards'):
                continue
            path = directory / name
            valid_type = path.suffix.lower() in MEDIA_TYPES and (
                path.parent == directory / 'media' or
                (path.parent == directory / 'cards' and path.suffix.lower() != '.mp4')
            )
            if not member.isreg() or not (name in JSON_FILES or valid_type):
                raise ValueError('数据包只支持 platedata 下的回放 JSON、录像和图片。')
            total += member.size
            if not member.size or total > MAX_UNPACKED or (name in JSON_FILES and member.size > MAX_JSON):
                raise ValueError(f'文件为空或超过数据包大小限制：{name}')
            path.parent.mkdir(exist_ok=True)
            with archive.extractfile(member) as content, path.open('xb') as output:
                shutil.copyfileobj(content, output)


def _unpack_archive(file, filename, directory):
    if not filename.lower().endswith('.zip'):
        with file.open('rb') as source:
            _unpack(source, directory)
        return
    with zipfile.ZipFile(file) as archive:
        entries = archive.infolist()
        bundles = [item for item in entries if not item.is_dir() and item.filename.lower().endswith(('.tar.gz', '.tgz'))]
        if len(entries) > 40 or len(bundles) != 1 or bundles[0].file_size > MAX_UPLOAD:
            raise ValueError('交付 ZIP 里应有且仅有一个 .tar.gz 回放数据包。')
        bundle = bundles[0]
        parent = bundle.filename.rpartition('/')[0]
        checksums = [item for item in entries if item.filename == (parent + '/' if parent else '') + 'SHA256SUMS.txt']
        if checksums:
            if len(checksums) != 1 or checksums[0].file_size > 8192:
                raise ValueError('交付 ZIP 中的校验文件格式有误。')
            expected = next((match.group(1) for line in archive.read(checksums[0]).decode('utf-8-sig').splitlines()
                             if (match := re.fullmatch(r'([0-9a-fA-F]{64}) [ *](.+)', line))
                             and match.group(2) == bundle.filename.rsplit('/', 1)[-1]), None)
            with archive.open(bundle) as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
            if not expected or actual != expected.casefold():
                raise ValueError('交付 ZIP 中的数据包 SHA-256 校验失败。')
        with archive.open(bundle) as source:
            _unpack(source, directory)


def _object(value):
    if not isinstance(value, dict):
        raise ValueError('回放 JSON 格式不正确。')
    return value


def _records(value):
    if not isinstance(value, list):
        raise ValueError('回放 JSON 中缺少应有的记录列表。')
    return [_object(item) for item in value]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('回放 JSON 中含有无效的数字。')
    return value


def _require_file(directory, group, name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in ('.', '..'):
        raise ValueError('回放 JSON 包含无效的素材引用。')
    path = directory / group / name
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f'数据包缺少素材：{group}/{name}')
    return path


def _media(directory, ref):
    prefix = '/api/v1/plate/media/'
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ValueError('回放素材应引用数据包内 /api/v1/plate/media/ 下的文件。')
    return _require_file(directory, 'media', ref[len(prefix):])


def _check_video(directory, metadata):
    video = _media(directory, metadata.get('src'))
    _media(directory, metadata.get('poster'))
    seconds = _number(metadata.get('seconds'))
    _number(metadata.get('offset_s'))
    if video.suffix.lower() != '.mp4' or seconds <= 0:
        raise ValueError('主回放和分屏录屏必须是有时长的 MP4。')
    capture = cv2.VideoCapture(str(video))
    try:
        first, _ = capture.read()
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = capture.get(cv2.CAP_PROP_FPS)
        codec = int(capture.get(cv2.CAP_PROP_FOURCC))
        fourcc = ''.join(chr((codec >> (8 * index)) & 255) for index in range(4)).lower()
        if not first or fps <= 0 or frames <= 0 or fourcc not in {'h264', 'avc1'} or abs(frames / fps - seconds) > 1:
            raise ValueError(f'视频须为可播放的 H.264 MP4，且实际时长须与记录一致：{video.name}')
    finally:
        capture.release()


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('回放 JSON 中含有无效的数字。')
    return number


def _validate(directory):
    documents = {}
    for name in sorted(JSON_FILES):
        file = directory / name
        if not file.is_file():
            raise ValueError(f'数据包缺少 {name}。')
        documents[name] = _object(json.loads(file.read_text(encoding='utf-8-sig'),
                                              parse_float=_finite_float, parse_constant=_finite_float))
    timeline, coverage, acceptance, route = (documents[name] for name in
        ('timeline.json', 'coverage.json', 'acceptance.json', 'media/path.json'))
    run = _object(timeline.get('mission')).get('run')
    rviz = _object(coverage.get('rviz'))
    batch = _object(acceptance.get('batch'))
    batch_run = batch.get('mission_id') or str(batch.get('batch_dir') or '').rstrip('/').rsplit('/', 1)[-1]
    if (not isinstance(run, str) or not NAME.fullmatch(run)
            or batch_run != run
            or rviz.get('run') != run or rviz.get('fallback')
            or run not in str(route.get('source') or '')):
        raise ValueError('主回放、验收、分屏录屏和航线须属于同一批次。')
    _check_video(directory, _object(timeline.get('video')))
    _check_video(directory, rviz)
    _media(directory, coverage.get('image'))
    for phase in _records(timeline.get('phases')):
        if phase.get('cover'):
            _media(directory, phase['cover'])
    _records(timeline.get('events'))
    for group in ('rows', 'coverage', 'slots'):
        _records(coverage.get(group))
    _records(route.get('cars'))
    _object(route.get('area'))
    steps = _records(route.get('steps'))
    if not steps:
        raise ValueError('航线没有可回放的路线点。')
    previous = -math.inf
    for step in steps:
        seconds = _number(step.get('t'))
        _number(step.get('x'))
        _number(step.get('y'))
        if seconds < previous or seconds < 0:
            raise ValueError('航线时间必须按顺序排列。')
        previous = seconds
    vehicles = _records(acceptance.get('vehicles'))
    _records(acceptance.get('summary'))
    _records(acceptance.get('violations'))
    if not vehicles or acceptance.get('available') is not True:
        raise ValueError('数据包没有可展示的车辆验收结果。')
    for vehicle in vehicles:
        _require_file(directory, 'cards', vehicle.get('card'))
    return {'run': run, 'steps': len(steps), 'vehicles': len(vehicles)}


def activate(file, filename, revision):
    destination = revision_root(revision)
    with TemporaryDirectory(dir=ROOT, prefix='.validate-') as work:
        staging = Path(work) / 'platedata'
        staging.mkdir()
        try:
            _unpack_archive(file, filename, staging)
            meta = _validate(staging)
        except (ValueError, tarfile.TarError, zipfile.BadZipFile, EOFError, UnicodeError, OverflowError, RuntimeError, cv2.error) as exc:
            message = str(exc) if type(exc) is ValueError else '无法读取压缩包或回放数据。'
            raise HTTPException(422, message) from exc
        with closing(_db()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS activation (id INTEGER PRIMARY KEY, current_id TEXT, previous_id TEXT)')
            row = _active(connection)
            connection.execute('CREATE TABLE IF NOT EXISTS activated_revisions (id TEXT PRIMARY KEY)')
            if connection.execute('SELECT id FROM activated_revisions WHERE id = ?', (revision,)).fetchone():
                return status(revision)
            if not destination.exists():
                meta['id'] = revision
                (staging / 'manifest.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
                staging.rename(destination)
            else:
                meta = status(revision)
            connection.execute('INSERT INTO activated_revisions VALUES (?)', (revision,))
            connection.execute('INSERT OR REPLACE INTO activation VALUES (1, ?, ?)', (revision, row[0] if row else None))
        return meta
