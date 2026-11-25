from __future__ import annotations

import json
import logging
import math
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, RLock
from typing import Literal
from urllib.parse import quote

import cv2
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from authapp.api import require_account
from browser_video import store_video
from inspection_subtitles import srt_bytes
from deployment_paths import backend_path
from roofapp import runtime
from uav_url_prefix import prefixed
from wallapp.intake import write_atomic

router = APIRouter()
log = logging.getLogger(__name__)
ROOF_MEDIA_DIR = backend_path('roofdata', 'media')
ROOF_REPORTS_DIR = backend_path('roofdata', 'reports')
VIDEO_SUFFIXES = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
JOB_LOCK = Lock()
REPORT_LOCK = RLock()
active_task: str | None = None


def _time():
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def _task_dir(task_id: str) -> Path:
    root = ROOF_MEDIA_DIR.resolve()
    folder = (root / task_id).resolve()
    if folder.parent != root or not folder.is_dir() or task_id.startswith('.'):
        raise HTTPException(404, '屋顶巡检任务不存在')
    return folder


def _report_dir(task_id: str) -> Path:
    return ROOF_REPORTS_DIR / task_id


def _load_json(path: Path, default):
    with REPORT_LOCK:
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            return data if default is None or isinstance(data, type(default)) else default
        except (OSError, ValueError):
            return default


def _save_json(path: Path, data):
    with REPORT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _video(folder: Path):
    return next((p for p in sorted(folder.iterdir()) if p.is_file()
                 and p.suffix.lower() in VIDEO_SUFFIXES and 'annotated' not in p.name.lower()), None)


def _media_url(collection: str, task_id: str, filename: str):
    return prefixed(f'/api/v1/roof/{collection}/{quote(task_id, safe="")}/{quote(filename, safe="")}')


def _defects(task_id: str):
    reports = _report_dir(task_id)
    if active_task == task_id:
        pending = _load_json(reports / 'analysis-in-progress.json', None)
        if pending is not None:
            return pending
    return _load_json(reports / 'defects.json', [])


def _read_task(task_id: str):
    folder = _task_dir(task_id)
    report = _report_dir(task_id)
    meta = _load_json(folder / 'task.json', {})
    video = _video(folder)
    subtitle = next((p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() == '.srt'), None)
    telemetry = _load_json(report / 'telemetry.json', [])
    analysis = _load_json(report / 'analysis.json', {})
    has_results = (report / 'defects.json').is_file()
    has_legacy_report = bool(telemetry) or (report / 'report.html').is_file()
    status = analysis.get('state', 'completed' if has_results or has_legacy_report else meta.get('status', 'uploaded'))
    error = analysis.get('error', '')
    if status == 'running' and task_id != active_task:
        status, error = 'failed', '检测中断，可以重新开始。'
    duration = meta.get('duration_sec') or max((point.get('video_time_ms', 0) for point in telemetry), default=0) / 1000
    return {
        'id': task_id, 'name': meta.get('name') or (video.stem if video else '建筑屋顶巡检任务'),
        'status': status, 'progress': analysis.get('progress', 100 if has_results else 0),
        'defect_count': len(_defects(task_id)), 'error': error, 'has_results': has_results,
        'video_url': _media_url('media', task_id, video.name) if video else '',
        'srt_url': _media_url('media', task_id, subtitle.name) if subtitle else '',
        'report_url': _media_url('reports', task_id, 'report.html') if (report / 'report.html').is_file() else '',
        'created_at': meta.get('created_at', ''), 'updated_at': meta.get('updated_at', ''),
        'duration_sec': duration, 'video_name': meta.get('video_name') or (video.name if video else ''),
        'srt_name': meta.get('srt_name') or (subtitle.name if subtitle else ''),
    }


def _public_defect(defect, task_id):
    replacements = {'閺夊倽宕?': '杂草堆积', '閼磋京姣?': '屋面脱皮', '缁夘垱鎸?': '积水区域'}
    label = defect.get('class_name', '屋面缺陷')
    result = {**defect, 'class_name': replacements.get(label, label)}
    evidence = defect.get('evidence_file')
    if evidence:
        result['screenshot_path'] = _media_url('reports', task_id, evidence)
    return result


@router.get('/tasks')
def get_roof_tasks():
    ROOF_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [_read_task(folder.name) for folder in ROOF_MEDIA_DIR.iterdir()
             if folder.is_dir() and not folder.is_symlink() and not folder.name.startswith('.')]
    return {'tasks': sorted(tasks, key=lambda task: (task['created_at'], task['id']), reverse=True)}


@router.post('/tasks/upload')
def upload_roof_task(task_name: str = Form(''), video: UploadFile = File(...),
                     srt: UploadFile | None = File(None), account=Depends(require_account)):
    original_name = (video.filename or 'roof-task.mp4').replace('\\', '/').rsplit('/', 1)[-1]
    if Path(original_name).suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(422, '请选择 MP4、MOV、AVI、MKV 或 WEBM 视频。')
    subtitle = (srt.filename or 'subtitle.srt').replace('\\', '/').rsplit('/', 1)[-1] if srt else ''
    if subtitle and Path(subtitle).suffix.lower() != '.srt':
        raise HTTPException(422, '定位字幕请使用 SRT 文件。')
    task_id = uuid.uuid4().hex[:12]
    staging = ROOF_MEDIA_DIR / ('.upload-' + task_id)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        if srt:
            write_atomic(staging / 'flight.srt', srt_bytes(srt))
        store_video(video.file, staging / 'inspection.mp4', original_name)
        now = _time()
        _save_json(staging / 'task.json', {'name': task_name.strip() or Path(original_name).stem,
                   'video_name': original_name, 'srt_name': subtitle, 'status': 'uploaded',
                   'created_at': now, 'updated_at': now})
        staging.rename(ROOF_MEDIA_DIR / task_id)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {'task_id': task_id}


@router.get('/model')
def get_roof_model():
    return runtime.model_status()


@router.post('/model')
def upload_roof_model(file: UploadFile = File(...), account=Depends(require_account)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.pt', '.onnx'}:
        raise HTTPException(422, '请选择屋顶巡检专用 YOLO PT 或 ONNX 权重。')
    target = runtime.ONNX_MODEL if suffix == '.onnx' else runtime.PT_MODEL
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, suffix=suffix, delete=False) as output:
        path = Path(output.name)
        try:
            size = 0
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise HTTPException(413, '模型文件不能超过 512 MB。')
                output.write(chunk)
            output.close()
            if not size:
                raise HTTPException(422, '模型文件为空。')
            if suffix == '.onnx' or runtime.inference_dependencies_available():
                try:
                    with runtime.MODEL_LOCK:
                        runtime.build_predictor(path)(np.zeros((320, 320, 3), dtype=np.uint8), .25)
                except ValueError as exc:
                    log.warning('Roof model upload validation failed: %s', exc)
                    raise HTTPException(422, str(exc)) from None
                except Exception:
                    log.exception('Roof model upload validation failed')
                    raise HTTPException(422, '模型无法完成试运行，请检查权重格式和推理环境。') from None
            with JOB_LOCK:
                if active_task:
                    raise HTTPException(409, '请等当前屋顶任务检测完成后再替换模型。')
                path.replace(target)
        finally:
            path.unlink(missing_ok=True)
    return runtime.model_status()


def _iou(first, second):
    l, t, r, b = first
    x, y, w, h = second
    area = max(0, min(r, w) - max(l, x)) * max(0, min(b, h) - max(t, y))
    return area / max((r-l)*(b-t) + (w-x)*(h-y) - area, 1e-9)


def _process_video(task_id, video, predict, confidence):
    report = _report_dir(task_id)
    staging = report / 'analysis-in-progress.json'
    progress_path = report / 'analysis.json'
    run = uuid.uuid4().hex[:12]
    completed = False
    processed = 0
    stream = cv2.VideoCapture(str(video))
    previous_defects = _load_json(report / 'defects.json', [])
    found = [item for item in previous_defects
             if item.get('review_status', 'pending') != 'pending' or not item.get('model_run')]
    recent = []
    try:
        fps = stream.get(cv2.CAP_PROP_FPS)
        if not stream.isOpened() or not math.isfinite(fps) or fps <= 0:
            raise ValueError('无法读取视频帧。')
        total = stream.get(cv2.CAP_PROP_FRAME_COUNT)
        step = max(1, round(fps))
        index = 0
        last_progress = -5
        while stream.grab():
            if index % step != 0:
                index += 1
                continue
            ok, frame = stream.retrieve()
            if not ok or frame is None:
                index += 1
                continue
            processed += 1
            timestamp = round(index / fps * 1000)
            height, width = frame.shape[:2]
            with runtime.MODEL_LOCK:
                detections = predict(frame, confidence)
            candidates = [item for item in recent if timestamp - item['last'] <= 2500]
            available = candidates.copy()
            for name, score, pixels in detections:
                box = [round(max(0., min(1., value / size)), 5) for value, size in zip(pixels, (width, height, width, height))]
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                previous = max((item for item in available if item['name'] == name),
                               key=lambda item: _iou(box, item['bbox']), default=None)
                if previous and _iou(box, previous['bbox']) >= .35:
                    previous.update(last=timestamp, bbox=box)
                    available.remove(previous)
                    continue
                candidates.append({'name': name, 'last': timestamp, 'bbox': box})
                if any(name == item.get('class_name') and item.get('bbox')
                       and abs(timestamp - item.get('video_time_ms', 0)) <= 2500 and _iou(box, item['bbox']) >= .45
                       for item in found if item.get('review_status') in {'confirmed', 'rejected'}):
                    continue
                identifier = uuid.uuid4().hex[:12]
                evidence = f'{run}-{identifier}.jpg'
                scale = min(1., 960 / width)
                thumbnail = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
                ok, jpeg = cv2.imencode('.jpg', thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if ok:
                    write_atomic(report / evidence, jpeg.tobytes())
                found.append({'id': identifier, 'class_name': name, 'confidence': round(score, 3), 'bbox': box,
                              'frame_index': index, 'video_time_ms': timestamp, 'review_status': 'pending',
                              'model_run': run, 'evidence_file': evidence if ok else ''})
            recent = candidates
            progress = min(99, int(index / total * 100)) if total and math.isfinite(total) else 0
            if progress >= last_progress + 4 or timestamp % 5000 < 100:
                _save_json(staging, found)
                _save_json(progress_path, {'state': 'running', 'progress': progress})
                last_progress = progress
            index += 1
        if not processed:
            raise ValueError('视频没有可检测的画面。')
        with REPORT_LOCK:
            _save_json(report / 'defects.json', found)
            completed = True
            _save_json(progress_path, {'state': 'completed', 'progress': 100})
    finally:
        stream.release()
        with REPORT_LOCK:
            staging.unlink(missing_ok=True)
        retained = {item.get('evidence_file') for item in found}
        obsolete = [report / item['evidence_file'] for item in previous_defects
                    if item.get('model_run') and item.get('evidence_file') and item['evidence_file'] not in retained]
        for path in (obsolete if completed else report.glob(f'{run}-*.jpg')):
            if path.parent == report:
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    log.debug('Evidence file is in use: %s', path.name)


def _run_analysis(task_id, video, predict, confidence):
    global active_task
    try:
        _process_video(task_id, video, predict, confidence)
    except Exception:
        log.exception('Roof analysis failed for %s', task_id)
        _save_json(_report_dir(task_id) / 'analysis.json',
                   {'state': 'failed', 'progress': 0, 'error': '检测未完成，请检查视频和模型后重试。'})
    finally:
        with JOB_LOCK:
            active_task = None


class AnalysisRequest(BaseModel):
    confidence: float = Field(.30, ge=.05, le=.95)


@router.post('/tasks/{task_id}/analysis', status_code=202)
def begin_analysis(task_id: str, background: BackgroundTasks, payload: AnalysisRequest,
                   account=Depends(require_account)):
    global active_task
    video = _video(_task_dir(task_id))
    if video is None:
        raise HTTPException(422, '这个任务没有可检测的视频。')
    status = runtime.model_status()
    if not status['ready']:
        raise HTTPException(503, status['message'])
    with JOB_LOCK:
        if active_task:
            raise HTTPException(409, '已有屋顶任务正在检测，请等待完成。')
        try:
            with runtime.MODEL_LOCK:
                predict = runtime.get_predictor(runtime.model_path())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            log.exception('Roof model could not be loaded')
            raise HTTPException(422, '无法加载屋顶模型，请检查权重格式。') from None
        _save_json(_report_dir(task_id) / 'analysis.json', {'state': 'running', 'progress': 0})
        active_task = task_id
    background.add_task(_run_analysis, task_id, video, predict, payload.confidence)
    return {'status': 'running'}


@router.get('/tasks/{task_id}')
def get_roof_task(task_id: str):
    return _read_task(task_id)


@router.get('/tasks/{task_id}/defects')
def get_roof_defects(task_id: str):
    _task_dir(task_id)
    return {'defects': [_public_defect(item, task_id) for item in _defects(task_id)]}


@router.get('/tasks/{task_id}/statistics')
def get_roof_statistics(task_id: str):
    defects = get_roof_defects(task_id)['defects']
    summary = {'total': len(defects), 'by_severity': {'high': 0, 'medium': 0, 'low': 0},
               'by_type': {}, 'by_status': {}}
    for item in defects:
        for column, key in [('by_severity', item.get('severity', 'low')),
                            ('by_type', item['class_name']), ('by_status', item.get('review_status', 'pending'))]:
            summary[column][key] = summary[column].get(key, 0) + 1
    return summary


@router.get('/tasks/{task_id}/telemetry')
def get_roof_telemetry(task_id: str):
    _task_dir(task_id)
    return {'telemetry': _load_json(_report_dir(task_id) / 'telemetry.json', [])}


@router.get('/tasks/{task_id}/trajectory')
def get_roof_trajectory(task_id: str):
    telemetry = get_roof_telemetry(task_id)['telemetry']
    points = [[row['longitude'], row['latitude']] for row in telemetry if 'longitude' in row and 'latitude' in row]
    return {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': points}, 'properties': {'task_id': task_id}}]}


@router.get('/tasks/{task_id}/map-meta')
def get_roof_map_meta(task_id: str):
    telemetry = get_roof_telemetry(task_id)['telemetry']
    if not telemetry:
        return {'center': [24.8334, 102.8399], 'zoom': 18}
    point = telemetry[len(telemetry) // 2]
    return {'center': [point.get('latitude', 24.8334), point.get('longitude', 102.8399)], 'zoom': 19}


class ReviewRequest(BaseModel):
    review_status: Literal['pending', 'confirmed', 'rejected']


def _review(task_id, defect_id, payload):
    path = _report_dir(task_id) / 'defects.json'
    with JOB_LOCK:
        if active_task == task_id:
            raise HTTPException(409, '请等待检测完成再复核。')
        rows = _load_json(path, [])
        defect = next((row for row in rows if row.get('id') == defect_id), None)
        if defect is None:
            return False
        defect['review_status'] = payload.review_status
        _save_json(path, rows)
        return True


@router.put('/tasks/{task_id}/defects/{defect_id}/review')
def review_task_defect(task_id: str, defect_id: str, payload: ReviewRequest, account=Depends(require_account)):
    _task_dir(task_id)
    if not _review(task_id, defect_id, payload):
        raise HTTPException(404, '缺陷记录不存在')
    return {'ok': True}


@router.put('/defects/{defect_id}/review')
def review_roof_defect(defect_id: str, payload: ReviewRequest, account=Depends(require_account)):
    for folder in ROOF_REPORTS_DIR.glob('*'):
        if folder.is_dir() and not folder.is_symlink() and folder.name != active_task and _review(folder.name, defect_id, payload):
            return {'ok': True}
    raise HTTPException(404, '缺陷记录不存在')


@router.get('/health')
def roof_health():
    return {'status': 'ready', 'tasks': len(get_roof_tasks()['tasks']), 'model': runtime.model_status()}
