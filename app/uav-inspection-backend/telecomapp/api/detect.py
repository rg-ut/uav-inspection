from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from telecomapp.detector import telecom_detector
from browser_video import store_video
from inspection_subtitles import MAX_BYTES, srt_bytes
from wallapp.intake import write_atomic
from deployment_paths import backend_path
from uav_url_prefix import prefixed

router = APIRouter()

MEDIA_DIR = backend_path('telecomdata', 'media')
TASKS_DIR = backend_path('telecom-tasks')
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)
SUPPORTED_VIDEO_SUFFIXES = {'.mp4', '.avi', '.mov', '.mkv'}
SUPPORTED_SUBTITLE_SUFFIXES = {'.srt', '.txt', '.vtt'}


class TelecomDetectRequest(BaseModel):
    image: str
    conf: float | None = None
    iou: float | None = None


class TelecomConfigRequest(BaseModel):
    confidence: float | None = None
    iou: float | None = None


class TelecomRenameTaskRequest(BaseModel):
    task_name: str


def _resolve_task_dir(task_id: str) -> Path:
    root = TASKS_DIR.resolve()
    target = (TASKS_DIR / task_id).resolve()
    if target != root and root in target.parents:
        return target
    raise HTTPException(status_code=400, detail='任务编号不合法')


def _read_task_meta(task_dir: Path) -> dict[str, str]:
    meta = {
        'task_id': task_dir.name,
        'task_name': task_dir.name,
        'video_name': '',
        'srt_name': '',
        'created_at': '',
    }
    meta_file = task_dir / 'meta.txt'
    if meta_file.exists():
        with meta_file.open('r', encoding='utf-8') as file:
            for raw in file:
                line = raw.strip()
                if line.startswith('task_name='):
                    meta['task_name'] = line[10:]
                elif line.startswith('video='):
                    meta['video_name'] = line[6:]
                elif line.startswith('srt='):
                    meta['srt_name'] = line[4:]
                elif line.startswith('created_at='):
                    meta['created_at'] = line[11:]

    if not meta['video_name']:
        for candidate in task_dir.iterdir():
            if candidate.suffix.lower() in SUPPORTED_VIDEO_SUFFIXES:
                meta['video_name'] = candidate.name
                break
    if not meta['srt_name']:
        for candidate in task_dir.iterdir():
            if candidate.name != 'meta.txt' and candidate.suffix.lower() in SUPPORTED_SUBTITLE_SUFFIXES:
                meta['srt_name'] = candidate.name
                break

    return meta


def _write_task_meta(task_dir: Path, meta: dict[str, str]) -> None:
    name = meta.get('task_name') or meta.get('video_name') or task_dir.name
    created = meta.get('created_at') or ''
    data = '\n'.join((f'task_name={name}', f"video={meta.get('video_name', '')}",
                      f"srt={meta.get('srt_name', '')}", f'created_at={created}', ''))
    write_atomic(task_dir / 'meta.txt', data.encode('utf-8'))


def _serialize_task(task_dir: Path) -> dict[str, object] | None:
    meta = _read_task_meta(task_dir)
    video_name = str(meta['video_name'])
    srt_name = str(meta['srt_name'])
    if not video_name or Path(video_name).name != video_name or not (task_dir / video_name).is_file():
        return None
    if Path(srt_name).name != srt_name or not (task_dir / srt_name).is_file():
        srt_name = ''
    created_at = meta['created_at']
    if not created_at or created_at == task_dir.name:
        created_at = datetime.fromtimestamp((task_dir / video_name).stat().st_mtime).isoformat(sep=' ', timespec='seconds')

    return {
        'task_id': meta['task_id'],
        'task_name': meta['task_name'],
        'video_name': video_name,
        'srt_name': srt_name,
        'created_at': created_at,
        'video_url': prefixed(f"/api/v1/telecom/tasks/{meta['task_id']}/{quote(video_name)}"),
        'srt_url': prefixed(f"/api/v1/telecom/tasks/{meta['task_id']}/{quote(srt_name)}") if srt_name else '',
    }


@router.get('/health')
def telecom_health() -> dict[str, object]:
    try:
        telecom_detector.load_model()
        return {
            'code': 200,
            'message': 'success',
            'data': {'status': 'ready', 'service_status': 'running', 'inference_available': True, 'model': 'loaded'},
        }
    except Exception as exc:
        return {
            'code': 200,
            'message': '基础服务正常，可选 AI 推理暂不可用',
            'data': {'status': 'not_ready', 'service_status': 'running', 'inference_available': False, 'error': str(exc)},
        }


@router.get('/videos')
async def telecom_videos() -> dict[str, object]:
    videos = []
    if MEDIA_DIR.exists():
        files = sorted(
            MEDIA_DIR.iterdir(),
            key=lambda item: (0 if item.name == 'demo_station_h264.mp4' else 1, item.name),
        )
        for file in files:
            if file.is_file() and file.suffix.lower() in SUPPORTED_VIDEO_SUFFIXES:
                videos.append({
                    'name': file.name,
                    'url': prefixed(f'/api/v1/telecom/media/{quote(file.name)}'),
                })
    return {
        'code': 200,
        'message': 'success',
        'data': {'videos': videos},
    }


@router.get('/tasks')
async def telecom_tasks() -> dict[str, object]:
    tasks: list[dict[str, object]] = []
    if not TASKS_DIR.exists():
        return {'code': 200, 'message': 'success', 'data': {'tasks': tasks}}

    for task_dir in sorted(TASKS_DIR.iterdir(), reverse=True):
        if not task_dir.is_dir() or task_dir.name.startswith('.'):
            continue

        task = _serialize_task(task_dir)
        if task is not None:
            tasks.append(task)

    return {'code': 200, 'message': 'success', 'data': {'tasks': tasks}}


@router.post('/tasks/upload')
def telecom_upload_task(
    task_name: str = Form(''),
    video: UploadFile = File(...),
    srt: UploadFile | None = File(None),
) -> dict[str, object]:
    original_name = (video.filename or 'video.mp4').replace('\\', '/').rsplit('/', 1)[-1]
    video_name = Path(original_name).stem + '.mp4'
    srt_name = (srt.filename or 'subtitle.srt').replace('\\', '/').rsplit('/', 1)[-1] if srt else ''
    if Path(original_name).suffix.lower() not in SUPPORTED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail='仅支持 mp4、avi、mov、mkv 视频文件')
    if srt and Path(srt_name).suffix.lower() not in SUPPORTED_SUBTITLE_SUFFIXES:
        raise HTTPException(status_code=400, detail='仅支持 srt、txt、vtt 字幕文件')

    task_id = uuid.uuid4().hex[:12]
    task_dir = TASKS_DIR / task_id
    staging = TASKS_DIR / ('.upload-' + task_id)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        if srt:
            if Path(srt_name).suffix.lower() == '.srt':
                subtitle = srt_bytes(srt)
            else:
                subtitle = srt.file.read(MAX_BYTES + 1)
                if not subtitle or len(subtitle) > MAX_BYTES:
                    raise HTTPException(422, '字幕为空或超过 64 MB。')
            (staging / srt_name).write_bytes(subtitle)
        store_video(video.file, staging / video_name, original_name)
        _write_task_meta(staging, {
            'task_name': ' '.join(task_name.split())[:100] or video_name,
            'video_name': video_name, 'srt_name': srt_name,
            'created_at': datetime.now().isoformat(sep=' ', timespec='seconds'),
        })
        staging.rename(task_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {'code': 200, 'message': 'success', 'data': _serialize_task(task_dir)}


@router.post('/tasks/{task_id}/rename')
async def telecom_rename_task(task_id: str, payload: TelecomRenameTaskRequest) -> dict[str, object]:
    task_dir = _resolve_task_dir(task_id)
    if not task_dir.exists() or not task_dir.is_dir():
        raise HTTPException(status_code=404, detail='任务不存在')

    task_name = ' '.join(payload.task_name.split())[:100]
    if not task_name:
        raise HTTPException(status_code=400, detail='任务名称不能为空')

    meta = _read_task_meta(task_dir)
    meta['task_name'] = task_name
    _write_task_meta(task_dir, meta)
    task = _serialize_task(task_dir)
    if task is None:
        raise HTTPException(status_code=404, detail='任务文件不完整')

    return {'code': 200, 'message': 'success', 'data': task}


@router.delete('/tasks/{task_id}')
async def telecom_delete_task(task_id: str) -> dict[str, object]:
    task_dir = _resolve_task_dir(task_id)
    if not task_dir.exists() or not task_dir.is_dir():
        raise HTTPException(status_code=404, detail='任务不存在')

    meta = _read_task_meta(task_dir)
    shutil.rmtree(task_dir)
    return {
        'code': 200,
        'message': 'success',
        'data': {
            'task_id': task_id,
            'task_name': meta.get('task_name', task_id),
            'video_name': meta.get('video_name', ''),
            'srt_name': meta.get('srt_name', ''),
        },
    }


@router.get('/config')
async def telecom_config() -> dict[str, object]:
    return {
        'code': 200,
        'message': 'success',
        'data': telecom_detector.update_config(),
    }


@router.post('/config')
async def telecom_update_config(payload: TelecomConfigRequest) -> dict[str, object]:
    return {
        'code': 200,
        'message': 'success',
        'data': telecom_detector.update_config(confidence=payload.confidence, iou=payload.iou),
    }


@router.get('/info')
def telecom_info() -> dict[str, object]:
    try:
        return {
            'code': 200,
            'message': 'success',
            'data': telecom_detector.get_info(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/detect')
def telecom_detect(payload: TelecomDetectRequest) -> JSONResponse:
    try:
        result = telecom_detector.detect_base64(payload.image, conf_threshold=payload.conf, iou_threshold=payload.iou)
        return JSONResponse({
            'code': 200,
            'message': 'success',
            'data': result,
        })
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
