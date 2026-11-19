"""Run video inference independently of browser playback."""
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import shutil
from tempfile import NamedTemporaryFile
from pathlib import Path
from threading import Event, Thread

import cv2
import numpy as np

from wallapp.detector import MODEL_PATH, wall_detector
from wallapp.workflow import ROOT, database, new_id

log = logging.getLogger(__name__)
wake = Event()
MODEL_DIR = ROOT / 'models'

# 三类缺陷（与老版外墙报告口径一致）；未知类别默认归为面砖脱落。
KIND_ALIASES = {
    'crack': 'Crack',
    'seepage': 'Seepage',
    'water_stain': 'Seepage',
    'water_damage': 'Seepage',
    'tile_spalling': 'TileSpalling',
    'tilespalling': 'TileSpalling',
    'spalling': 'TileSpalling',
    'wall_damage': 'TileSpalling',
    'facade_damage': 'TileSpalling',
    'damage': 'TileSpalling',
    'hollowing': 'TileSpalling',
}
ZONE_ROWS = ('上部', '中部', '下部')
ZONE_COLUMNS = ('左侧', '中央', '右侧')


def finding_kind(name):
    return KIND_ALIASES.get((name or '').strip().lower(), 'TileSpalling')


def finding_zone(bbox, shape):
    height, width = shape[:2]
    x1, y1, x2, y2 = (float(value) for value in bbox)
    center_x = (x1 + x2) / 2 / max(1.0, width)
    center_y = (y1 + y2) / 2 / max(1.0, height)
    return f"{ZONE_ROWS[min(2, int(center_y * 3))]}{ZONE_COLUMNS[min(2, int(center_x * 3))]}"


def model_path(version):
    if version == 'installed':
        return MODEL_PATH
    onnx = MODEL_DIR / (version + '.onnx')
    return onnx if onnx.is_file() or not onnx.with_suffix('.pt').is_file() else onnx.with_suffix('.pt')


def fingerprint(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def snapshot_installed():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    version = fingerprint(MODEL_PATH)
    if model_path(version).is_file():
        return version
    with NamedTemporaryFile(dir=MODEL_DIR, suffix='.pt', delete=False) as target:
        temporary = Path(target.name)
        try:
            with MODEL_PATH.open('rb') as source:
                shutil.copyfileobj(source, target)
            target.close()
            version = fingerprint(temporary)
            temporary.replace(MODEL_DIR / (version + '.pt'))
            return version
        finally:
            temporary.unlink(missing_ok=True)


def onnx_output(net, image):
    net.setInput(cv2.dnn.blobFromImage(image, 1 / 255, (640, 640), swapRB=True))
    result = np.asarray(net.forward())
    if result.ndim != 3 or result.shape[0] != 1 or result.shape[1] != 5 or result.shape[2] <= 5:
        raise ValueError('Expected YOLO detection output of shape [1, 5, N]')
    return result[0].T


def validate_onnx(path):
    net = cv2.dnn.readNetFromONNX(np.frombuffer(path.read_bytes(), dtype=np.uint8))
    onnx_output(net, np.zeros((640, 640, 3), dtype=np.uint8))


def onnx_predictor(path):
    net = cv2.dnn.readNetFromONNX(np.frombuffer(path.read_bytes(), dtype=np.uint8))

    def predict(frame, confidence, iou):
        height, width = frame.shape[:2]
        scale = min(640 / width, 640 / height)
        new_width, new_height = round(width * scale), round(height * scale)
        pad_x, pad_y = (640 - new_width) // 2, (640 - new_height) // 2
        canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
        canvas[pad_y:pad_y + new_height, pad_x:pad_x + new_width] = cv2.resize(frame, (new_width, new_height))
        boxes, scores = [], []
        for x, y, w, h, score in onnx_output(net, canvas):
            if not np.isfinite([x, y, w, h, score]).all() or score < confidence or score > 1:
                continue
            x1, x2 = np.clip([(x - w / 2 - pad_x) / scale, (x + w / 2 - pad_x) / scale], 0, width)
            y1, y2 = np.clip([(y - h / 2 - pad_y) / scale, (y + h / 2 - pad_y) / scale], 0, height)
            if x2 > x1 and y2 > y1:
                boxes.append([int(x1), int(y1), max(1, int(x2 - x1)), max(1, int(y2 - y1))])
                scores.append(float(score))
        indexes = cv2.dnn.NMSBoxes(boxes, scores, confidence, iou)
        result = []
        for index in np.asarray(indexes).reshape(-1):
            x, y, w, h = boxes[index]
            result.append({'name': 'TileSpalling', 'conf': scores[index], 'bbox': [x, y, x + w, y + h]})
        return result

    return predict


def overlap(box, other):
    x1, y1, x2, y2 = box
    a1, b1, a2, b2 = other
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    union = (x2 - x1) * (y2 - y1) + (a2 - a1) * (b2 - b1) - intersection
    return intersection / union if union else 0


@dataclass
class Track:
    id: str
    bbox: list
    first: float
    last: float
    score: float


def add_frame(run, time, detections, frame, tracks):
    available = {track.id for track in tracks}
    evidence_dir = ROOT / 'evidence' / run['task_id']
    evidence_dir.mkdir(parents=True, exist_ok=True)
    updates = []
    for detection in sorted(detections, key=lambda item: item['conf'], reverse=True):
        kind = finding_kind(detection.get('name'))
        box = detection['bbox']
        score = detection['conf']
        candidates = [track for track in tracks if track.id in available and 0 <= time - track.last <= 1.5]
        match = max(candidates, key=lambda track: overlap(track.bbox, box), default=None)
        if match is not None and overlap(match.bbox, box) >= 0.35:
            available.remove(match.id)
        else:
            match = Track(new_id(), box, time, time, -1)
            tracks.append(match)
        match.bbox = box
        match.last = time
        evidence = None
        if score > match.score:
            evidence = f'{match.id}-{int(time * 1000)}.jpg'
            evidence_frame = frame.copy()
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(evidence_frame, (x1, y1), (x2, y2), (0, 185, 255), 3)
            encoded, jpeg = cv2.imencode('.jpg', evidence_frame)
            if not encoded:
                raise OSError('无法保存检测证据')
            (evidence_dir / evidence).write_bytes(jpeg.tobytes())
            match.score = score
        updates.append((match, box, evidence))
    tracks[:] = [track for track in tracks if time - track.last <= 1.5]
    if not updates:
        return
    with database(write=True) as conn:
        state = conn.execute('SELECT state FROM runs WHERE id=?', (run['id'],)).fetchone()['state']
        if state != 'running':
            for _, _, evidence in updates:
                if evidence:
                    (evidence_dir / evidence).unlink(missing_ok=True)
            return
        for track, box, evidence in updates:
            if evidence:
                conn.execute(
                    """INSERT INTO findings(id,task_id,run_id,kind,zone,first_time,last_time,evidence_time,score,bbox,evidence,source)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                       last_time=excluded.last_time,evidence_time=excluded.evidence_time,score=excluded.score,
                       bbox=excluded.bbox,evidence=excluded.evidence WHERE findings.state='pending'""",
                    (track.id, run['task_id'], run['id'], kind, finding_zone(box, frame.shape),
                     track.first, time, time, track.score, json.dumps(box), evidence, 'model'),
                )
                current = conn.execute('SELECT evidence FROM findings WHERE id=?', (track.id,)).fetchone()
                if current['evidence'] != evidence:
                    (evidence_dir / evidence).unlink(missing_ok=True)
            else:
                conn.execute("UPDATE findings SET last_time=? WHERE id=? AND state='pending'", (time, track.id))


def run_video(run, task, stop):
    path = model_path(run['model'])
    detect = onnx_predictor(path) if path.suffix == '.onnx' else wall_detector.video_predictor(path)
    capture = cv2.VideoCapture(str(ROOT / 'media' / task['id'] / task['video']))
    count = 0
    tracks = []
    covered_windows = set()
    position = 0.0
    previous_pts = -1.0
    next_sample = 0.0
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or fps <= 0:
            raise ValueError('无法读取视频')
        period = 1 / run['sample_fps'] if run['sample_fps'] else 0
        frame_index = 0
        while not stop.is_set():
            if frame_index % max(1, int(fps)) == 0:
                with database() as conn:
                    state = conn.execute('SELECT state FROM runs WHERE id=?', (run['id'],)).fetchone()['state']
                if state == 'cancelled':
                    return
            valid, frame = capture.read()
            if not valid:
                break
            frame_index += 1
            pts = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if math.isfinite(pts) and pts > previous_pts and pts >= position:
                position = pts
            elif frame_index > 1:
                position += 1 / fps
            previous_pts = pts
            if position + 0.00001 < next_sample:
                continue
            next_sample = (int(position / period + 0.00001) + 1) * period if period else 0
            detections = detect(frame, run['confidence'], run['iou'])
            add_frame(run, position, detections, frame, tracks)
            covered_windows.add(round(position * fps) if not period else int(position / period + 0.00001))
            count += 1
            if count == 1 or count % 10 == 0:
                with database(write=True) as conn:
                    conn.execute('UPDATE runs SET frame_count=?,position=?,coverage=? WHERE id=?',
                                 (count, position, len(covered_windows) * (period or 1 / fps), run['id']))
        if stop.is_set():
            return
        if not count:
            raise ValueError('未读到可分析的视频帧')
        duration = task['duration'] or position + 1 / fps
        coverage = min(duration, len(covered_windows) * (period or 1 / fps))
        with database(write=True) as conn:
            result = conn.execute("""UPDATE runs SET state='completed',frame_count=?,position=?,coverage=?
                                     WHERE id=? AND state='running'""", (count, position, coverage, run['id']))
            if result.rowcount:
                if coverage >= duration * 0.99:
                    conn.execute("""UPDATE findings SET state='superseded'
                                    WHERE task_id=? AND run_id<>? AND state='pending'""",
                                 (task['id'], run['id']))
    finally:
        capture.release()


def work(stop):
    while not stop.is_set():
        with database(write=True) as conn:
            run = conn.execute("SELECT * FROM runs WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if run:
                task = conn.execute('SELECT * FROM tasks WHERE id=?', (run['task_id'],)).fetchone()
                conn.execute("UPDATE runs SET state='running' WHERE id=?", (run['id'],))
        if not run:
            wake.wait(2)
            wake.clear()
            continue
        try:
            run_video(run, task, stop)
        except Exception:
            log.exception('Wall inspection run %s failed', run['id'])
            with database(write=True) as conn:
                conn.execute("""UPDATE runs SET state='failed',error=? WHERE id=? AND state='running'""",
                             ('分析未完成，请检查视频和模型后重试', run['id']))


def start_worker():
    with database(write=True) as conn:
        conn.execute("""UPDATE findings SET state='superseded' WHERE state='pending'
                         AND run_id IN (SELECT id FROM runs WHERE state='running')""")
        conn.execute("""UPDATE runs SET state='queued',frame_count=0,position=0,coverage=0
                         WHERE state='running'""")
    stop = Event()
    worker = Thread(target=work, args=(stop,), daemon=True, name='wall-video-analysis')
    worker.start()
    return stop
