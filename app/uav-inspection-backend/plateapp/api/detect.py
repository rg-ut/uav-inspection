from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from plateapp.detector import plate_detector
from plateapp.recognizer import plate_recognizer
from deployment_paths import backend_path
from uav_url_prefix import prefixed

router = APIRouter()

# 数据落点遵循平台约定：<数据根>/uav-inspection-backend/<domain>data/。
# 早期版本把 data 放在包目录里，会跟着 source 一起打进镜像层，容器重建（--force-recreate）
# 后运行期写入的 timeline/coverage 全丢，且与 deployment_readiness 的判定口径打架。
_PACKAGED_DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
_RUNTIME_DATA_DIR = backend_path('platedata')
# Choose one batch directory so replay metadata, cards and media use the same source.
DATA_DIR = (
    _RUNTIME_DATA_DIR
    if any((_RUNTIME_DATA_DIR / name).is_file() for name in ('acceptance.json', 'timeline.json', 'coverage.json'))
    else _PACKAGED_DATA_DIR
)
ACCEPTANCE_PATH = DATA_DIR / 'acceptance.json'
CARDS_DIR = DATA_DIR / 'cards'

# 过程回放与覆盖质量（2026-11-15 加）。timeline/coverage 是离线脚本产出的 JSON，
# 媒体（回放视频、阶段封面、热力图）走 /api/v1/plate/media 静态挂载。
TIMELINE_PATH = DATA_DIR / 'timeline.json'
COVERAGE_PATH = DATA_DIR / 'coverage.json'
# 2026-11-20：四宫格第四格由「全局建图」改成俯视路径图，
# 数据从 mission_r1.log 与 stage2_r*.log 的 GOTO 序列重建，见 platedata/path.json 的 source 字段。
# 同一天把它挪进 media/ 下（原 platedata/path.json）：这是给前端读的静态数据，放 media 里
# 走既有的 /plate/media 静态挂载就能取到。这样即便后端进程是旧代码（没有 /plate/path 路由，
# 用户双击「打开车牌巡检.bat」常驻的 8002 就是这种情况，uvicorn 没开 --reload 不会热更），
# 前端 404 后回退到 /plate/media/path.json 仍能出图，不必为了一个路由重启服务。
MEDIA_DIR = DATA_DIR / 'media'
PATH_PATH = MEDIA_DIR / 'path.json'


def _versioned(url: str, base_dir: Path) -> str:
    """给静态资源 URL 追加内容戳，防止换批次后客户端继续用旧文件。

    静态挂载不带 Cache-Control，浏览器对同 URL 的 mp4/jpg 会强缓存。实测把 p9 的回放
    换成 p10 后，页面仍显示旧的 21:44（1304 s）而不是新的 20:12（1212 s），
    普通刷新和 F5 都无效，必须硬清缓存。加上 ?v=<mtime> 后 URL 变了，自然重新拉取。
    结果卡（cards/*.jpg）是同一文件名换内容，不吃这套就会一直显示旧卡，故一并走这里。
    幂等：URL 已带查询串时替换掉，不叠加——`x.mp4?v=1?v=2` 会让服务端把
    `x.mp4?v=1` 当文件名，直接 404。
    """
    base, _sep, _query = url.partition('?')
    name = base.rsplit('/', 1)[-1]
    try:
        return '%s?v=%d' % (base, int((base_dir / name).stat().st_mtime))
    except OSError:
        return url


def _media_versioned(url: str) -> str:
    return _versioned(url, MEDIA_DIR)


def _cards_versioned(url: str) -> str:
    return _versioned(url, CARDS_DIR)

# 视频检测是同步执行的，必须有明确上限，否则一个大文件就能把工作进程占满。
MAX_VIDEO_FRAMES = 40
MAX_VIDEO_SECONDS = 60.0


class PlateDetectRequest(BaseModel):
    image: str
    conf: float | None = None
    iou: float | None = None
    recognize: bool = True


class PlateConfigRequest(BaseModel):
    confidence: float | None = None
    iou: float | None = None
    max_plates: int | None = None


def _load_acceptance() -> dict[str, Any] | None:
    if not ACCEPTANCE_PATH.is_file():
        return None
    try:
        return json.loads(ACCEPTANCE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None


def _load_json_file(path: Path) -> dict[str, Any] | None:
    """读离线脚本产出的 JSON；坏文件按「没有」处理，不让页面 500。"""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _fallback_rviz() -> dict[str, Any] | None:
    """仓库自带批次没有 RViz 分屏录屏时，用主回放视频顶上。

    前端第 2、3 格原本从 1920x1080 的四宫格合成片里各裁一个象限，那份文件只随
    实机批次交付（见 docs/plate-replay.md），仓库里没有。缺了它两格会变成
    「RViz 录屏未载入」，四宫格有半边是空的，看起来就像整个模块坏了。

    这里退回 timeline.video 的同一段录屏：offset_s 与主时钟一致，前端检测到
    fallback=true 后改用全幅取景、不再做四象限裁剪。带真实 rviz 段的批次
    永远走原路径，不受影响。
    """
    timeline = _load_json_file(TIMELINE_PATH)
    if not timeline:
        return None
    video = timeline.get('video') or {}
    src = str(video.get('src') or '')
    if not src:
        return None
    size = str(video.get('size') or '1280x720')
    try:
        width, height = (int(part) for part in size.lower().split('x', 1))
    except (ValueError, AttributeError):
        width, height = 1280, 720
    return {
        'src': src,
        'poster': '',
        'seconds': float(video.get('seconds') or 0),
        'width': width,
        'height': height,
        'fps': float(video.get('fps') or 15),
        'run': str((timeline.get('mission') or {}).get('run') or ''),
        'note': '本批次未单独采集 RViz 分屏录屏，这里同步回放全场俯视画面；'
                '接入实机批次后会自动换成机载相机与点云视角。',
        'offset_s': float(video.get('offset_s') or 0),
        'same_batch': True,
        'fallback': True,
    }


def _fallback_path() -> dict[str, Any] | None:
    """仓库自带批次没有 GOTO 日志时，从车位与阶段时间轴重建一条示意航线。

    path.json 由 scripts/onsite/gen_path_json.py 在实机上从 stage2_r*.log 生成，
    同样不随仓库交付。缺了它第四格只剩一句「路径数据未载入」，而且前端会连打
    两个 404（/plate/path 与 /plate/media/path.json）。

    这里用 coverage.slots（15 个车位实测坐标）、acceptance.violations（车→车位
    映射）与 timeline.phases（各阶段墙钟区间）拼一条随播放推进的示意路径：
    巡航阶段走弓字形扫描点，逐车拍摄阶段在每辆车旁落一个视点，收尾回场地中心。
    t 用墙钟秒，和前端 pathNow = videoTime + video.offset_s 同一条时间轴。
    """
    coverage = _load_json_file(COVERAGE_PATH)
    timeline = _load_json_file(TIMELINE_PATH)
    if not coverage or not timeline:
        return None
    phases = [p for p in (timeline.get('phases') or []) if isinstance(p, dict)]
    if not phases:
        return None
    rect = (coverage.get('source') or {}).get('rect') or [-13.7, 13.7, -9.2, 9.2]
    try:
        x0, x1, y0, y1 = (float(v) for v in rect)
    except (TypeError, ValueError):
        x0, x1, y0, y1 = -13.7, 13.7, -9.2, 9.2
    slots = {
        str(s['name']): (float(s['x']), float(s['y']))
        for s in (coverage.get('slots') or [])
        if isinstance(s, dict) and 'name' in s and 'x' in s and 'y' in s
    }
    acceptance = _load_acceptance() or {}
    plates = {
        str(v.get('car')): str(v.get('plate') or '')
        for v in (acceptance.get('vehicles') or []) if isinstance(v, dict)
    }
    cars: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (acceptance.get('violations') or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get('car') or '')
        slot = str(item.get('slot') or '')
        if not name or name in seen or slot not in slots:
            continue
        seen.add(name)
        cx, cy = slots[slot]
        cars.append({'name': name, 'x': cx, 'y': cy, 'yaw': 0.0, 'plate': plates.get(name, '')})
    if not cars:
        return None

    steps: list[dict[str, Any]] = []

    def add(t: float, car: str, leg: str, x: float, y: float, kind: str) -> None:
        steps.append({
            'i': len(steps) + 1, 't': round(float(t), 1),
            'car': car, 'leg': leg, 'd': 0.0,
            'x': round(float(x), 3), 'y': round(float(y), 3),
            'yaw': 0.0, 'kind': kind,
        })

    # 阶段一：沿弓字形航线扫全场（4 行 × 2 个端点）
    survey = phases[0]
    s0, s1 = float(survey.get('t0') or 0), float(survey.get('t1') or 0)
    rows = [-6.0, -2.0, 2.0, 6.0]
    for r, y in enumerate(rows):
        xs = (x0 + 2.5, x1 - 2.5) if r % 2 == 0 else (x1 - 2.5, x0 + 2.5)
        for j, x in enumerate(xs):
            add(s0 + (s1 - s0) * (r * 2 + j + 1) / (len(rows) * 2 + 1),
                'FLEET', 'overview/all', x, y, 'overview')

    # 逐车拍摄阶段：每轮在车旁落一个视点，轮次越靠后待拍的车越少
    inspects = [p for p in phases[1:] if str(p.get('key') or '').startswith('inspect')]
    if not inspects:
        inspects = phases[1:-1] or phases[1:]
    for k, phase in enumerate(inspects):
        p0, p1 = float(phase.get('t0') or 0), float(phase.get('t1') or 0)
        batch = cars if k == 0 else cars[: max(1, len(cars) // (k + 1))]
        for j, car in enumerate(batch):
            t = p0 + (p1 - p0) * (j + 1) / (len(batch) + 1)
            add(t, car['name'], 'front/near', car['x'], car['y'] - 1.6, 'capture')

    # 收尾：回场地中心
    wrap = phases[-1]
    w0, w1 = float(wrap.get('t0') or 0), float(wrap.get('t1') or 0)
    add((w0 + w1) / 2, 'FLEET', 'wrap/home', 0.0, 0.0, 'transit')

    steps.sort(key=lambda s: s['t'])
    for idx, step in enumerate(steps):
        step['i'] = idx + 1

    return {
        'source': 'fallback: coverage.slots + timeline.phases + acceptance.violations',
        'note': '默认演示批次没有 GOTO 日志，这条航线由车位坐标与阶段时间轴重建，'
                '仅用于展示随时间推进的飞行路线；接入实机批次后会自动换成日志复算结果。',
        'area': {'x0': x0, 'x1': x1, 'y0': y0, 'y1': y1},
        'cars': cars,
        'steps': steps,
    }


@router.get('/health')
async def plate_health() -> dict[str, object]:
    """只报告服务是否在跑，不去加载模型——加模型可能耗时数秒。"""
    status = plate_recognizer.status()
    try:
        available = plate_detector.model is not None
    except Exception:
        available = False
    return {
        'code': 200,
        'message': 'success' if available else '基础服务正常，检测模型尚未加载',
        'data': {
            'status': 'ready' if available else 'idle',
            'service_status': 'running',
            'detector_loaded': available,
            'recognizer_available': status['available'],
            'acceptance_available': ACCEPTANCE_PATH.is_file(),
        },
    }


@router.get('/info')
async def plate_info() -> dict[str, object]:
    try:
        return {'code': 200, 'message': 'success', 'data': plate_detector.get_info()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get('/config')
async def plate_config() -> dict[str, object]:
    return {'code': 200, 'message': 'success', 'data': plate_detector.get_config()}


@router.post('/config')
async def plate_update_config(payload: PlateConfigRequest) -> dict[str, object]:
    return {
        'code': 200,
        'message': 'success',
        'data': plate_detector.update_config(
            confidence=payload.confidence, iou=payload.iou, max_plates=payload.max_plates),
    }


@router.post('/detect')
async def plate_detect(payload: PlateDetectRequest) -> JSONResponse:
    try:
        result = plate_detector.detect_base64(
            payload.image, conf_threshold=payload.conf,
            iou_threshold=payload.iou, recognize=payload.recognize)
        return JSONResponse({'code': 200, 'message': 'success', 'data': result})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/detect/upload')
async def plate_detect_upload(
    image: UploadFile = File(...),
    conf: float | None = None,
    recognize: bool = True,
) -> JSONResponse:
    """页面直接传文件时用这个，省掉前端转 base64 的开销。"""
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail='上传的文件是空的')
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail='无法解码图片，请确认上传的是 JPG/PNG')
    try:
        result = plate_detector.detect(frame, conf_threshold=conf, recognize=recognize)
        return JSONResponse({'code': 200, 'message': 'success', 'data': result})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/detect/video')
async def plate_detect_video(
    video: UploadFile = File(...),
    sample_step: int = 15,
    conf: float | None = None,
    recognize: bool = True,
) -> JSONResponse:
    """按固定间隔抽帧检测，汇总去重后的车牌清单。

    同步执行且有硬上限（MAX_VIDEO_FRAMES / MAX_VIDEO_SECONDS），超出的部分
    直接截断并在响应里说明，避免长视频把工作进程占满。
    """
    suffix = Path(video.filename or 'clip.mp4').suffix or '.mp4'
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            while chunk := await video.read(1024 * 1024):
                tmp.write(chunk)
            temp_path = Path(tmp.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'视频写入失败：{exc}') from exc

    try:
        capture = cv2.VideoCapture(str(temp_path))
        if not capture.isOpened():
            raise HTTPException(status_code=400, detail='无法打开视频，请确认格式是 MP4/AVI/MOV')

        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, int(sample_step))
        limit_frames = int(min(total_frames or 0, MAX_VIDEO_SECONDS * fps)) or None

        plates: dict[str, dict[str, Any]] = {}
        colors: dict[str, int] = {}
        frames: list[dict[str, Any]] = []
        processed = 0
        best: dict[str, Any] | None = None
        index = 0

        while len(frames) < MAX_VIDEO_FRAMES:
            ok, frame = capture.read()
            if not ok:
                break
            if limit_frames and index >= limit_frames:
                break
            if index % step == 0:
                try:
                    result = plate_detector.detect(frame, conf_threshold=conf, recognize=recognize)
                except FileNotFoundError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                processed += 1
                second = round(index / fps, 2)
                kept = []
                for detection in result['detections']:
                    colors[detection['color']] = colors.get(detection['color'], 0) + 1
                    text = detection['text']
                    if text:
                        record = plates.setdefault(text, {
                            'text': text, 'count': 0, 'best_conf': 0.0,
                            'color': detection['color'], 'color_cn': detection['color_cn'],
                            'first_seen_s': second,
                        })
                        record['count'] += 1
                        if detection['conf'] > record['best_conf']:
                            record['best_conf'] = detection['conf']
                    kept.append({
                        'bbox': detection['bbox'], 'conf': detection['conf'],
                        'text': text, 'color': detection['color'],
                    })
                if kept:
                    frames.append({'frame': index, 'second': second, 'plates': kept})
                    if best is None or len(kept) > len(best['plates']):
                        best = {'frame': index, 'second': second,
                                'image': result['annotated_image'], 'plates': kept}
            index += 1
        capture.release()

        truncated = bool(limit_frames and index >= limit_frames) or len(frames) >= MAX_VIDEO_FRAMES
        return JSONResponse({'code': 200, 'message': 'success', 'data': {
            'fps': round(float(fps), 2),
            'total_frames': total_frames,
            'sampled_frames': processed,
            'hits': len(frames),
            'truncated': truncated,
            'plates': sorted(plates.values(), key=lambda item: (-item['count'], item['text'])),
            'unique_count': len(plates),
            'color_stats': colors,
            'frames': frames[:12],
            'best_frame': best,
            'recognizer': plate_recognizer.status(),
            'limits': {'max_sampled_frames': MAX_VIDEO_FRAMES,
                       'max_seconds': MAX_VIDEO_SECONDS,
                       'sample_step': step},
        }})
    finally:
        temp_path.unlink(missing_ok=True)


# response_model=None：返回值可能是 dict 也可能是 JSONResponse（缺数据显示 404），
# 不关掉自动响应模型的话 FastAPI 会在导入期就报「Invalid args for response field」。
@router.get('/acceptance', response_model=None)
async def plate_acceptance() -> dict[str, object] | JSONResponse:
    """离线验收结果回放：没有模型权重时，页面仍有可展示的真实数据。"""
    payload = _load_acceptance()
    if payload is None:
        # 缺数据既要让页面看得懂（体内 code），也要让网关/监控判得对（HTTP 404）。
        return JSONResponse(status_code=404, content={
            'code': 404, 'message': '未找到验收回放数据', 'data': {'available': False}})
    for vehicle in payload.get('vehicles', []):
        card = vehicle.get('card')
        vehicle['card_url'] = _cards_versioned(prefixed(f'/api/v1/plate/cards/{card}')) if card else None
    return {'code': 200, 'message': 'success', 'data': payload}


@router.get('/replay', response_model=None)
async def plate_replay() -> dict[str, object] | JSONResponse:
    """任务过程回放：阶段时间轴 + 回放视频地址 + 关键事件。

    数据由 ai/analysis/v4e_timeline_20261115.py 从 driver.log 与 fsm.json 解析，
    视频是 watch_raw.mp4 压到 854x480 / CRF 32（200 MB → 2.6 MB）。
    """
    payload = _load_json_file(TIMELINE_PATH)
    if payload is None:
        return JSONResponse(status_code=404, content={
            'code': 404, 'message': '未找到回放数据', 'data': {'available': False}})

    video = payload.get('video') or {}
    for key in ('src', 'poster'):
        if video.get(key):
            video[key] = _media_versioned(prefixed(str(video[key])))
    payload['video'] = video

    for phase in payload.get('phases') or []:
        if phase.get('cover'):
            phase['cover'] = _media_versioned(prefixed(str(phase['cover'])))

    return {'code': 200, 'message': 'success', 'data': payload}


@router.get('/path', response_model=None)
async def plate_path() -> dict[str, object] | JSONResponse:
    """逐腿路径与视点：从任务日志的 GOTO 序列重建，供四宫格第四格画俯视路径图。

    来源：~/runs/mission_20261116_120348 的 stage2_r1.log / stage2_r2.log / inspect_r*.yaml。
    计算口径：front/* 视点按车轴方位前推 d 米复算平面位置；transit/* 平面取车坐标
    （实际在车正上方，图上不体现高度）；FLEET 取场地中心。高度一律不画。
    """
    payload = _load_json_file(PATH_PATH)
    if payload is None:
        # 没有实机 GOTO 日志时用仓库自带批次现场重建一条示意航线，
        # 让第四格仍有随时间推进的内容，前端也不再连打两个 404。
        payload = _fallback_path()
    if payload is None:
        return JSONResponse(status_code=404, content={
            'code': 404, 'message': '未找到路径数据', 'data': {'available': False}})
    return {'code': 200, 'message': 'success', 'data': payload}


@router.get('/coverage', response_model=None)
async def plate_coverage() -> dict[str, object] | JSONResponse:
    """L4 覆盖质量：可观测区格数、各帧数门限占比、分排统计、15 车位质量。

    分母口径：贴墙矩形（x±13.7 / y±9.2）再扣掉 7 辆车体遮挡区。
    不扣车体会把物理上必然看不到的车底格算进分母，把覆盖率压低约 3 个点。
    """
    payload = _load_json_file(COVERAGE_PATH)
    if payload is None:
        return JSONResponse(status_code=404, content={
            'code': 404, 'message': '未找到覆盖质量数据', 'data': {'available': False}})

    if payload.get('image'):
        payload['image'] = _media_versioned(prefixed(str(payload['image'])))
    # RViz 全流程录屏（2026-11-16 加）：同样要带内容戳，否则换批后浏览器放旧片。
    # 仓库自带批次没有这段元数据时回退到主回放视频，避免第 2、3 格空着。
    rviz = payload.get('rviz')
    if not isinstance(rviz, dict):
        fallback = _fallback_rviz()
        if fallback is not None:
            payload['rviz'] = fallback
            rviz = fallback
    if isinstance(rviz, dict):
        for key in ('src', 'poster'):
            if rviz.get(key):
                rviz[key] = _media_versioned(prefixed(str(rviz[key])))
    return {'code': 200, 'message': 'success', 'data': payload}


# 一份完整数据包必须具备的条目（相对 DATA_DIR）。rviz 录屏不在此列：旧批次没有它，
# 后端会用主回放顶上（见 _fallback_rviz），缺了不算装坏。
_REQUIRED_ENTRIES = (
    'timeline.json',
    'coverage.json',
    'acceptance.json',
    'media/replay.mp4',
    'media/path.json',
    'media/heatmap.png',
    'media/poster.jpg',
)


def _dataset_summary() -> dict[str, Any]:
    """当前在用的数据包：目录、来源、批次、缺了哪几项。"""
    timeline = _load_json_file(TIMELINE_PATH) or {}
    mission = timeline.get('mission') or {}
    video = timeline.get('video') or {}
    coverage = _load_json_file(COVERAGE_PATH) or {}
    # 报「实际在放什么」而不是「文件里写了什么」：旧批次没有 rviz 段，/coverage
    # 会临时顶上主回放视频，这里跟着取同一个结果，否则 summary 说「没有录屏」
    # 而页面明明在放，运维会以为两边对不上。
    rviz = coverage.get('rviz')
    if not isinstance(rviz, dict) or not rviz:
        rviz = _fallback_rviz() or {}
    path = _load_json_file(PATH_PATH) or {}
    acceptance = _load_acceptance() or {}

    present = [name for name in _REQUIRED_ENTRIES if (DATA_DIR / name).is_file()]
    cards = CARDS_DIR.is_dir() and any(CARDS_DIR.glob('*.jpg'))
    missing = [name for name in _REQUIRED_ENTRIES if name not in present]
    if not cards:
        missing.insert(0, 'cards/')

    return {
        'data_dir': str(DATA_DIR),
        'source': 'runtime' if DATA_DIR == _RUNTIME_DATA_DIR else 'packaged',
        'runtime_dir': str(_RUNTIME_DATA_DIR),
        'packaged_dir': str(_PACKAGED_DATA_DIR),
        'mission': {
            'run': mission.get('run'),
            'id': mission.get('id'),
            'started_at': mission.get('started_at'),
        },
        'video': {
            'seconds': video.get('seconds'),
            'offset_s': video.get('offset_s'),
        },
        'rviz': {
            'run': rviz.get('run'),
            'offset_s': rviz.get('offset_s'),
            'fallback': bool(rviz.get('fallback')),
        },
        'path': {
            'source': path.get('source'),
            'steps': len(path.get('steps') or []),
            # 文件不在时 /path 会用 slots + 阶段时间轴现场拼一条示意航线
            'fallback': not PATH_PATH.is_file(),
        },
        'coverage_map': (coverage.get('source') or {}).get('map_dir'),
        'cards': len(list(CARDS_DIR.glob('*.jpg'))) if cards else 0,
        'vehicles': len(acceptance.get('vehicles') or []),
        'present': present,
        'missing': missing,
    }


@router.get('/dataset')
async def plate_dataset() -> dict[str, object]:
    """回放数据包自检：装完数据后 curl 一次就知道网站在用哪一批、缺什么。

    为什么要单开一个接口：数据包要人工拷进 UAV_DATA_DIR 再重启才生效，而
    DATA_DIR 在导入时就定了（运行期目录里只要有任何一份 JSON 就整目录切过去），
    所以「拷漏一份」不会报错，只会让页面静默少一块；反过来，若运行期目录是空的，
    后端会安静地退回镜像里的旧批次，页面上看不出区别，只有顶栏的任务编号不同。
    这里把目录 / 来源 / 批次 / 缺失项一次说清，避免靠肉眼比对顶栏猜。
    """
    return {'code': 200, 'message': 'success', 'data': _dataset_summary()}
