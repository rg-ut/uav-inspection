"""Authenticated inspection workflow, separate from the legacy demonstration."""
import csv
import io
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel, Field
from datetime import date
from functools import lru_cache
from threading import Lock

from optional_inference import inference_dependencies_available
from authapp.api import require_account
from uav_url_prefix import prefixed
from wallapp import analysis, intake, workflow_report
from wallapp.api import assistant, live
from wallapp.workflow import KIND_LABELS, ROOT, admin, database, new_id, now, record, summary, task_for

router = APIRouter()
router.include_router(intake.router)
router.include_router(live.router)
router.include_router(assistant.router)


def answer(value):
    return {'code': 200, 'message': 'success', 'data': value}


def url(task_id, suffix):
    return prefixed(f'/api/v1/wall/workflow/tasks/{task_id}/{suffix}')


def task_data(row):
    return {
        'id': row['id'], 'name': row['name'], 'building': row['building'], 'facade': row['facade'],
        'duration': row['duration'], 'offset': row['offset'], 'created_at': row['created_at'],
        'video_url': url(row['id'], 'video'),
        'subtitle_url': url(row['id'], 'subtitle') if row['subtitle'] else None,
    }


def finding_data(row):
    result = dict(row)
    result['bbox'] = json.loads(result['bbox'])
    result['image_url'] = url(row['task_id'], f'findings/{row["id"]}/image') if row['evidence'] else None
    result.pop('evidence')
    return result


def order_data(conn, row):
    item = dict(row)
    item['finding_ids'] = [r[0] for r in conn.execute('SELECT finding_id FROM order_findings WHERE order_id=?', (row['id'],))]
    item['photos'] = [{
        'kind': r['kind'], 'url': url(row['task_id'], f'orders/{row["id"]}/photos/{r["id"]}'),
    } for r in conn.execute('SELECT * FROM attachments WHERE order_id=? ORDER BY created_at', (row['id'],))]
    return item


def accessible_orders(conn, task, user):
    rows = conn.execute('SELECT * FROM work_orders WHERE task_id=? ORDER BY created_at DESC,id', (task['id'],))
    return [order_data(conn, row) for row in rows if can_manage(task, user) or is_assignee(row, user)]


def can_manage(task, user):
    return task['owner'] == user or admin(user)


def is_assignee(order, user):
    return order['assignee'].casefold() == user.casefold() and order['state'] not in {'draft', 'cancelled'}


@router.get('/tasks')
def list_tasks(account=Depends(require_account)):
    user = account['username']
    with database() as conn:
        rows = conn.execute('SELECT * FROM tasks ORDER BY created_at DESC,id') if admin(user) else conn.execute(
            """SELECT * FROM tasks t WHERE owner=? OR EXISTS (
                SELECT 1 FROM work_orders o WHERE o.task_id=t.id AND o.assignee=? COLLATE NOCASE
                AND o.state NOT IN ('draft','cancelled')) ORDER BY created_at DESC,id""", (user, user))
        return answer([task_data(row) for row in rows])


@router.get('/tasks/{task_id}')
def detail(task_id: str, account=Depends(require_account)):
    user = account['username']
    with database() as conn:
        task = task_for(conn, task_id, user)
        manager = can_manage(task, user)
        orders = accessible_orders(conn, task, user)
        allowed = {fid for order in orders for fid in order['finding_ids']}
        findings = [finding_data(row) for row in conn.execute(
            "SELECT * FROM findings WHERE task_id=? AND state<>'superseded' ORDER BY first_time,id", (task_id,))
            if manager or row['id'] in allowed]
        runs = [dict(row) for row in conn.execute(
            'SELECT * FROM runs WHERE task_id=? ORDER BY rowid DESC', (task_id,))] if manager else []
        counts = summary(conn, task_id) if manager else None
        return answer({'task': task_data(task), 'findings': findings, 'orders': orders,
                       'runs': runs, 'counts': counts, 'can_manage': manager})


@router.get('/tasks/{task_id}/video')
def task_video(task_id: str, account=Depends(require_account)):
    with database() as conn:
        task = task_for(conn, task_id, account['username'])
        folder = ROOT / 'media' / task_id
        playback = folder / 'playback.mp4'
        return FileResponse(playback if playback.is_file() else folder / task['video'])


@router.get('/tasks/{task_id}/subtitle')
def task_subtitle(task_id: str, account=Depends(require_account)):
    with database() as conn:
        task = task_for(conn, task_id, account['username'])
        if not task['subtitle']:
            raise HTTPException(404, '该任务暂无字幕')
        return FileResponse(ROOT / 'media' / task_id / task['subtitle'], media_type='text/plain')


class Offset(BaseModel):
    seconds: float = Field(ge=-86400, le=86400)


@router.put('/tasks/{task_id}/subtitle-offset')
def offset(task_id: str, payload: Offset, account=Depends(require_account)):
    with database(write=True) as conn:
        task_for(conn, task_id, account['username'], write=True)
        conn.execute('UPDATE tasks SET offset=? WHERE id=?', (payload.seconds, task_id))
    return answer({'seconds': payload.seconds})


@router.get('/models')
def list_models(account=Depends(require_account)):
    versions = []
    if analysis.MODEL_PATH.is_file():
        versions.append({'id': 'installed', 'name': '服务器 PT 权重',
                         'ready': inference_dependencies_available(),
                         'reason': '' if inference_dependencies_available() else '服务器尚未安装 PT 推理依赖'})
    for metadata in sorted(analysis.MODEL_DIR.glob('*.json')):
        if metadata.with_suffix('.onnx').is_file():
            try:
                info = json.loads(metadata.read_text(encoding='utf-8'))
                versions.append({'id': metadata.stem, 'name': str(info['name']), 'ready': True, 'reason': ''})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return answer({'models': versions, 'can_upload': True, 'upload_hint': ''})


@router.post('/models')
def upload_model(file: UploadFile = File(...), account=Depends(require_account)):
    filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if Path(filename).suffix.lower() != '.onnx':
        raise HTTPException(422, '请选择单类别 YOLO ONNX 模型，导出时关闭模型内合并框')
    analysis.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=analysis.MODEL_DIR, suffix='.onnx', delete=False) as output:
        path = Path(output.name)
        try:
            limit = 256 * 1024 * 1024
            size = 0
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, '模型超过 256 MB')
                output.write(chunk)
            output.close()
            version = analysis.fingerprint(path)
            try:
                analysis.validate_onnx(path)
            except (ValueError, OSError, analysis.cv2.error):
                raise HTTPException(422, '模型无法读取，请检查输入尺寸及单类别输出格式') from None
            path.replace(analysis.model_path(version))
            info = {'id': version, 'name': filename}
            intake.write_atomic(analysis.MODEL_DIR / (version + '.json'), json.dumps(info, ensure_ascii=False).encode())
            return answer(info)
        finally:
            output.close()
            path.unlink(missing_ok=True)


class RunRequest(BaseModel):
    model: str = Field(pattern=r'^(installed|[a-f0-9]{64})$')
    sensitivity: float = Field(ge=0.05, le=0.95)
    iou: float = Field(default=0.45, ge=0.05, le=0.95)
    mode: Literal['sample', 'all'] = 'sample'


@router.post('/tasks/{task_id}/runs')
def start_run(task_id: str, payload: RunRequest, account=Depends(require_account)):
    user = account['username']
    with database() as conn:
        task_for(conn, task_id, user, write=True)
    path = analysis.model_path(payload.model)
    if not path.is_file():
        raise HTTPException(422, '请选择已安装的检测模型')
    if path.suffix == '.pt' and not inference_dependencies_available():
        raise HTTPException(503, 'PT 权重已保存，但服务器未安装 requirements-inference.txt；请先安装推理依赖或添加 ONNX 模型。')
    version = payload.model
    if version == 'installed':
        # Copy the legacy model into the versioned store so a run always uses fixed weights.
        version = analysis.snapshot_installed()
    with database(write=True) as conn:
        task_for(conn, task_id, user, write=True)
        active = conn.execute("SELECT id FROM runs WHERE task_id=? AND state IN ('queued','running')", (task_id,)).fetchone()
        if active:
            raise HTTPException(409, '该任务已有分析在运行')
        run_id = new_id()
        conn.execute(
            'INSERT INTO runs(id,task_id,state,model,confidence,iou,sample_fps,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (run_id, task_id, 'queued', version, round(1 - payload.sensitivity, 4), payload.iou,
             10 if payload.mode == 'sample' else 0, now()),
        )
        record(conn, task_id, run_id, user, 'analysis_started')
    analysis.wake.set()
    return answer({'id': run_id})


@router.post('/tasks/{task_id}/runs/{run_id}/cancel')
def cancel_run(task_id: str, run_id: str, account=Depends(require_account)):
    user = account['username']
    with database(write=True) as conn:
        task_for(conn, task_id, user, write=True)
        result = conn.execute("""UPDATE runs SET state='cancelled' WHERE id=? AND task_id=?
                                  AND state IN ('queued','running')""", (run_id, task_id))
        if not result.rowcount:
            raise HTTPException(409, '该分析已结束')
        record(conn, task_id, run_id, user, 'analysis_cancelled')
    return answer({'id': run_id})


class Review(BaseModel):
    state: Literal['pending', 'confirmed', 'dismissed']
    kind: Literal['Crack', 'Seepage', 'TileSpalling'] | None = None
    floor: str = Field(default='', max_length=60)
    note: str = Field(default='', max_length=1500)


def get_finding(conn, task_id, finding_id):
    row = conn.execute('SELECT * FROM findings WHERE id=? AND task_id=?', (finding_id, task_id)).fetchone()
    if not row:
        raise HTTPException(404, '未找到该记录')
    return row


def linked_order(conn, finding_id):
    return conn.execute(
        """SELECT o.id FROM work_orders o JOIN order_findings f ON o.id=f.order_id
           WHERE f.finding_id=? AND o.state<>'cancelled'""", (finding_id,),
    ).fetchone()


@router.patch('/tasks/{task_id}/findings/{finding_id}')
def review(task_id: str, finding_id: str, payload: Review, account=Depends(require_account)):
    user = account['username']
    with database(write=True) as conn:
        task_for(conn, task_id, user, write=True)
        before = get_finding(conn, task_id, finding_id)
        if payload.state != 'confirmed' and linked_order(conn, finding_id):
            raise HTTPException(409, '该记录已关联工单，请先处理工单')
        conn.execute('UPDATE findings SET state=?,floor=?,note=?,kind=? WHERE id=?',
                     (payload.state, payload.floor.strip(), payload.note.strip(),
                      payload.kind or before['kind'], finding_id))
        record(conn, task_id, finding_id, user, 'finding_reviewed', f'{before["state"]} → {payload.state}')
        return answer(finding_data(get_finding(conn, task_id, finding_id)))


class MergeRequest(BaseModel):
    into: str = Field(pattern=r'^[a-f0-9]{32}$')
    ids: list[str] = Field(min_length=1, max_length=50)


@router.post('/tasks/{task_id}/findings/merge')
def merge(task_id: str, payload: MergeRequest, account=Depends(require_account)):
    user = account['username']
    with database(write=True) as conn:
        task_for(conn, task_id, user, write=True)
        target = get_finding(conn, task_id, payload.into)
        if target['state'] not in {'confirmed', 'pending'}:
            raise HTTPException(422, '请选择一条可复核的记录作为主记录')
        for finding_id in set(payload.ids) - {payload.into}:
            candidate = get_finding(conn, task_id, finding_id)
            if candidate['state'] not in {'pending', 'confirmed'} or linked_order(conn, finding_id):
                raise HTTPException(409, '所选记录已处理或已关联工单')
            conn.execute("UPDATE findings SET state='merged' WHERE id=?", (finding_id,))
            record(conn, task_id, finding_id, user, 'finding_merged', payload.into)
    return answer({'id': payload.into})


@router.get('/tasks/{task_id}/findings/{finding_id}/image')
def evidence(task_id: str, finding_id: str, account=Depends(require_account)):
    user = account['username']
    with database() as conn:
        task = task_for(conn, task_id, user)
        if not can_manage(task, user) and not any(finding_id in item['finding_ids'] for item in accessible_orders(conn, task, user)):
            raise HTTPException(404, '未找到该证据')
        finding = get_finding(conn, task_id, finding_id)
        if not finding['evidence']:
            raise HTTPException(404, '未找到该证据')
        return FileResponse(ROOT / 'evidence' / task_id / finding['evidence'], media_type='image/jpeg')


class DraftRequest(BaseModel):
    finding_ids: list[str] = Field(min_length=1, max_length=50)


@router.post('/tasks/{task_id}/orders')
def draft(task_id: str, payload: DraftRequest, account=Depends(require_account)):
    user = account['username']
    ids = set(payload.finding_ids)
    with database(write=True) as conn:
        task_for(conn, task_id, user, write=True)
        orders = set()
        for finding_id in ids:
            finding = get_finding(conn, task_id, finding_id)
            if finding['state'] != 'confirmed':
                raise HTTPException(422, '请先确认所选缺陷')
            linked = linked_order(conn, finding_id)
            if linked:
                orders.add(linked['id'])
        if orders:
            if len(orders) == 1:
                order = get_order(conn, task_id, next(iter(orders)))
                data = order_data(conn, order)
                if set(data['finding_ids']) == ids:
                    return answer(data)
            raise HTTPException(409, '部分缺陷已有关联工单')
        order_id = new_id()
        conn.execute('INSERT INTO work_orders(id,task_id,state,created_at) VALUES(?,?,?,?)', (order_id, task_id, 'draft', now()))
        conn.executemany('INSERT INTO order_findings VALUES(?,?)', [(order_id, finding_id) for finding_id in sorted(ids)])
        record(conn, task_id, order_id, user, 'order_created')
        return answer(order_data(conn, get_order(conn, task_id, order_id)))


def get_order(conn, task_id, order_id):
    row = conn.execute('SELECT * FROM work_orders WHERE id=? AND task_id=?', (order_id, task_id)).fetchone()
    if not row:
        raise HTTPException(404, '未找到该工单')
    return row


class OrderUpdate(BaseModel):
    action: Literal['save', 'dispatch', 'accept', 'submit', 'complete', 'return', 'cancel']
    assignee: str | None = Field(default=None, max_length=64)
    priority: Literal['normal', 'urgent'] | None = None
    due_on: date | None = None
    instructions: str | None = Field(default=None, max_length=2000)
    completion: str | None = Field(default=None, max_length=2000)
    reason: str = Field(default='', max_length=1000)


@router.patch('/tasks/{task_id}/orders/{order_id}')
def update_order(task_id: str, order_id: str, payload: OrderUpdate, account=Depends(require_account)):
    from authapp.api import account_exists

    user = account['username']
    with database(write=True) as conn:
        task = task_for(conn, task_id, user)
        order = get_order(conn, task_id, order_id)
        manager = can_manage(task, user)
        if not manager and (not is_assignee(order, user) or payload.action not in {'accept', 'submit'}):
            raise HTTPException(403, '你没有权限执行该操作')
        state = order['state']
        transitions = {
            'save': ({'draft'}, 'draft'),
            'dispatch': ({'draft'}, 'assigned'),
            'accept': ({'assigned'}, 'in_progress'),
            'submit': ({'in_progress'}, 'review'),
            'complete': ({'review'}, 'completed'),
            'return': ({'review'}, 'in_progress'),
            'cancel': ({'draft', 'assigned', 'in_progress', 'review'}, 'cancelled'),
        }
        allowed, next_state = transitions[payload.action]
        if state not in allowed:
            raise HTTPException(409, '工单状态已变化，请刷新后重试')
        assignee, priority, due_on, instructions, completion = (
            order['assignee'], order['priority'], order['due_on'], order['instructions'], order['completion'])
        if payload.action in {'save', 'dispatch'}:
            assignee = payload.assignee.strip() if payload.assignee is not None else assignee
            priority = payload.priority or priority
            due_on = payload.due_on.isoformat() if payload.due_on else due_on
            instructions = payload.instructions.strip() if payload.instructions is not None else instructions
        if payload.action == 'dispatch' and (not assignee or not due_on or not account_exists(assignee)):
            raise HTTPException(422, '请填写已注册的维修人员账号和截止日期')
        if payload.action in {'return', 'cancel'} and not payload.reason.strip():
            raise HTTPException(422, '请填写原因')
        if payload.action == 'submit':
            completion = (payload.completion or '').strip()
            kinds = {row[0] for row in conn.execute('SELECT kind FROM attachments WHERE order_id=?', (order_id,))}
            if not completion or not {'before', 'after'} <= kinds:
                raise HTTPException(422, '请填写完成说明，并上传维修前后照片')
        conn.execute("""UPDATE work_orders SET state=?,assignee=?,priority=?,due_on=?,instructions=?,completion=?
                        WHERE id=?""", (next_state, assignee, priority, due_on, instructions, completion, order_id))
        record(conn, task_id, order_id, user, f'order_{payload.action}',
               f'{state} → {next_state} {payload.reason.strip()}'.strip())
        return answer(order_data(conn, get_order(conn, task_id, order_id)))


@router.post('/tasks/{task_id}/orders/{order_id}/photos/{kind}')
def add_photo(task_id: str, order_id: str, kind: Literal['before', 'after'],
              file: UploadFile = File(...), account=Depends(require_account)):
    user = account['username']
    with database(write=True) as conn:
        task = task_for(conn, task_id, user)
        order = get_order(conn, task_id, order_id)
        if (not can_manage(task, user) and not is_assignee(order, user)) or order['state'] not in {'assigned', 'in_progress'}:
            raise HTTPException(403, '当前工单不能上传照片')
        data = file.file.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, '照片超过 10 MB')
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError('photo dimensions')
                image = image.convert('RGB')
                image.thumbnail((1920, 1920))
                buffer = io.BytesIO()
                image.save(buffer, 'JPEG', quality=88)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, '请选择有效照片') from None
        photo_id = new_id()
        folder = ROOT / 'photos' / task_id
        folder.mkdir(parents=True, exist_ok=True)
        filename = photo_id + '.jpg'
        intake.write_atomic(folder / filename, buffer.getvalue())
        conn.execute('INSERT INTO attachments VALUES(?,?,?,?,?)', (photo_id, order_id, kind, filename, now()))
        record(conn, task_id, order_id, user, 'photo_uploaded', kind)
        return answer({'url': url(task_id, f'orders/{order_id}/photos/{photo_id}')})


@router.get('/tasks/{task_id}/orders/{order_id}/photos/{photo_id}')
def photo(task_id: str, order_id: str, photo_id: str, account=Depends(require_account)):
    user = account['username']
    with database() as conn:
        task = task_for(conn, task_id, user)
        order = get_order(conn, task_id, order_id)
        if not can_manage(task, user) and not is_assignee(order, user):
            raise HTTPException(404, '未找到该照片')
        row = conn.execute('SELECT * FROM attachments WHERE id=? AND order_id=?', (photo_id, order_id)).fetchone()
        if not row:
            raise HTTPException(404, '未找到该照片')
        return FileResponse(ROOT / 'photos' / task_id / row['filename'], media_type='image/jpeg')


def safe_csv(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


@router.get('/tasks/{task_id}/export')
def export_csv(task_id: str, account=Depends(require_account)):
    with database() as conn:
        task = task_for(conn, task_id, account['username'], write=True)
        buf = io.StringIO()
        writer = csv.writer(buf)
        state_names = {'pending': '待复核', 'confirmed': '已确认', 'dismissed': '已排除',
                       'draft': '草稿', 'assigned': '待接单', 'in_progress': '维修中',
                       'review': '待验收', 'completed': '已完成'}
        writer.writerow(['任务', '楼栋', '立面', '缺陷编号', '缺陷类型', '缺陷方位', '复核状态', '楼层', '视频秒数', '工单编号', '工单状态'])
        rows = conn.execute("""SELECT f.*,o.id AS order_id,o.state AS order_state FROM findings f
                               LEFT JOIN (order_findings ofd JOIN work_orders o ON o.id=ofd.order_id AND o.state<>'cancelled')
                               ON ofd.finding_id=f.id WHERE f.task_id=? AND f.state NOT IN ('merged','superseded')
                               ORDER BY f.first_time""",
                            (task_id,))
        for row in rows:
            writer.writerow(map(safe_csv, [task['name'], task['building'], task['facade'], row['id'],
                                          KIND_LABELS.get(row['kind'], row['kind']), row['zone'],
                                          state_names[row['state']], row['floor'], row['evidence_time'],
                                          row['order_id'] or '', state_names.get(row['order_state'], '')]))
        return Response('\ufeff' + buf.getvalue(), media_type='text/csv',
                        headers={'Content-Disposition': f'attachment; filename="wall-{task_id}.csv"'})


@router.get('/tasks/{task_id}/report')
def export_pdf(task_id: str, account=Depends(require_account)):
    with database() as conn:
        task = task_for(conn, task_id, account['username'], write=True)
        counts = summary(conn, task_id)
        findings = conn.execute('SELECT * FROM findings WHERE task_id=? ORDER BY first_time,id', (task_id,)).fetchall()
        runs = conn.execute('SELECT * FROM runs WHERE task_id=? ORDER BY rowid DESC', (task_id,)).fetchall()
    folder = ROOT / 'reports' / task_id
    folder.mkdir(parents=True, exist_ok=True)
    report_id = new_id()
    with NamedTemporaryFile(dir=folder, suffix='.pdf', delete=False) as output:
        temporary = Path(output.name)
        output.close()
        try:
            workflow_report.generate(temporary, task, counts, findings, runs)
            destination = folder / (report_id + '.pdf')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(destination, media_type='application/pdf', filename=f'wall-{task_id}.pdf')


_preview_lock = Lock()


@lru_cache(maxsize=1)
def preview_predictor(version):
    path = analysis.model_path(version.split(":")[0])
    return analysis.onnx_predictor(path) if path.suffix == '.onnx' else analysis.wall_detector.video_predictor(path)


class LiveFrame(BaseModel):
    model: str = Field(pattern=r'^(installed|[a-f0-9]{64})$')
    sensitivity: float = Field(ge=0.05, le=0.95)
    image: str = Field(max_length=500000)


@router.post('/live/{device_id}/detect')
def preview_frame(device_id: str, payload: LiveFrame, account=Depends(require_account)):
    live.device(device_id, account['username'])
    return detect_preview(payload)


@router.post('/tasks/{task_id}/detect')
def task_preview(task_id: str, payload: LiveFrame, account=Depends(require_account)):
    with database() as conn:
        task_for(conn, task_id, account['username'], write=True)
    return detect_preview(payload)


def detect_preview(payload: LiveFrame):
    import base64
    import binascii
    from io import BytesIO
    from PIL import UnidentifiedImageError
    import numpy as np
    import time

    try:
        binary = base64.b64decode(payload.image, validate=True)
        with Image.open(BytesIO(binary)) as image:
            if image.width > 960 or image.height > 960 or image.format != 'JPEG':
                raise ValueError()
            frame = analysis.cv2.cvtColor(np.asarray(image.convert('RGB')), analysis.cv2.COLOR_RGB2BGR)
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, '画面格式不正确') from None
    if not analysis.model_path(payload.model).is_file():
        raise HTTPException(422, '请先选择已安装的检测模型')
    if not _preview_lock.acquire(blocking=False):
        raise HTTPException(429, '检测正在处理上一帧')
    try:
        path = analysis.model_path(payload.model)
        if path.suffix == '.pt' and not inference_dependencies_available():
            raise HTTPException(503, 'PT 权重需要安装 requirements-inference.txt；可改用 ONNX 模型。')
        version = payload.model + ':' + str(path.stat().st_mtime_ns)
        started = time.perf_counter()
        predict = preview_predictor(version)
        boxes = [item['bbox'] for item in predict(frame, 1 - payload.sensitivity, 0.45)]
        return answer({'boxes': boxes, 'width': frame.shape[1], 'height': frame.shape[0],
                       'inference_ms': round((time.perf_counter() - started) * 1000, 1)})
    except (ValueError, RuntimeError, OSError, analysis.cv2.error):
        raise HTTPException(503, '检测暂不可用，请检查所选模型') from None
    finally:
        _preview_lock.release()
