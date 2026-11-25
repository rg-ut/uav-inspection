"""Run roof-specific YOLO weights on uploaded video frames."""
from functools import lru_cache
from pickle import UnpicklingError
from threading import RLock

import cv2
import numpy as np

from deployment_paths import backend_path
from optional_inference import inference_dependencies_available, inference_device, model_signature

MODEL_DIR = backend_path('roofapp')
PT_MODEL = MODEL_DIR / 'roof-best.pt'
ONNX_MODEL = MODEL_DIR / 'roof-best.onnx'
LABELS = ('杂草堆积', '屋面脱皮', '积水区域')
MODEL_LOCK = RLock()


def model_path():
    candidates = [path for path in (ONNX_MODEL, PT_MODEL) if path.is_file()]
    if ONNX_MODEL in candidates and not inference_dependencies_available():
        return ONNX_MODEL
    return max(candidates, key=lambda path: path.stat().st_mtime_ns, default=PT_MODEL)


def model_status():
    path = model_path()
    if not path.is_file():
        return {'ready': False, 'name': '', 'message': '请添加屋顶巡检模型。'}
    if path.suffix == '.pt' and not inference_dependencies_available():
        return {'ready': False, 'name': path.name, 'message': 'PT 权重需要服务器的 AI 推理依赖，或可改用 ONNX。'}
    return {'ready': True, 'name': path.name, 'message': '可开始视频检测。'}


def load_onnx(path):
    net = cv2.dnn.readNetFromONNX(np.frombuffer(path.read_bytes(), dtype=np.uint8))

    def predict(frame, confidence):
        h, w = frame.shape[:2]
        scale = 640 / max(w, h)
        sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
        x0, y0 = (640 - sw) // 2, (640 - sh) // 2
        image = np.full((640, 640, 3), 114, dtype=np.uint8)
        image[y0:y0 + sh, x0:x0 + sw] = cv2.resize(frame, (sw, sh))
        net.setInput(cv2.dnn.blobFromImage(image, 1 / 255, (640, 640), swapRB=True))
        output = np.asarray(net.forward())
        if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] not in (5, 7):
            raise ValueError('ONNX 需使用 640 输入、关闭内置 NMS 的单类别或三类别 YOLO 检测模型。')
        names = LABELS if output.shape[1] == 7 else ('屋面缺陷',)
        candidates = {name: ([], []) for name in names}
        for row in output[0].T:
            if not np.isfinite(row).all():
                continue
            class_id = int(np.argmax(row[4:]))
            score = float(row[4 + class_id])
            if not confidence <= score <= 1:
                continue
            cx, cy, bw, bh = map(float, row[:4])
            left, right = map(int, np.clip([(cx - bw / 2 - x0) / scale, (cx + bw / 2 - x0) / scale], 0, w))
            top, bottom = map(int, np.clip([(cy - bh / 2 - y0) / scale, (cy + bh / 2 - y0) / scale], 0, h))
            if right > left and bottom > top:
                boxes, scores = candidates[names[class_id]]
                boxes.append([left, top, right - left, bottom - top])
                scores.append(score)
        result = []
        for name, (boxes, scores) in candidates.items():
            for idx in np.asarray(cv2.dnn.NMSBoxes(boxes, scores, confidence, .45)).reshape(-1):
                x, y, width, height = boxes[int(idx)]
                result.append((name, scores[int(idx)], [x, y, x + width, y + height]))
        return result

    return predict


def load_pt_predictor(path):
    if not inference_dependencies_available():
        raise ValueError('PT 权重需要服务器安装 AI 推理依赖。')

    import torch
    from torch import nn
    from ultralytics.models.yolo.detect import DetectionPredictor
    from ultralytics.models.yolo.obb import OBBPredictor
    from ultralytics.models.yolo.segment import SegmentationPredictor
    from ultralytics.nn import modules, tasks
    from ultralytics.nn.modules import block

    predictors = {
        tasks.DetectionModel: ('detect', DetectionPredictor),
        tasks.SegmentationModel: ('segment', SegmentationPredictor),
        tasks.OBBModel: ('obb', OBBPredictor),
    }
    torch_layers = (
        nn.ModuleList, nn.Sequential, nn.Identity, nn.Conv2d, nn.ConvTranspose2d,
        nn.BatchNorm2d, nn.MaxPool2d, nn.AvgPool2d, nn.AdaptiveAvgPool2d,
        nn.Upsample, nn.SiLU, nn.ReLU, nn.ReLU6, nn.GELU, nn.LeakyReLU,
        nn.Hardswish, nn.Sigmoid,
    )
    yolo_layers = ('Conv', 'Conv2', 'ConvTranspose', 'DWConv', 'DWConvTranspose2d', 'Concat',
                   'Focus', 'Bottleneck', 'BottleneckCSP', 'C2f', 'C3', 'C3k2', 'C2PSA',
                   'SPP', 'SPPF', 'DFL', 'Attention', 'Proto', 'Detect', 'Segment', 'OBB',
                   'v10Detect', 'SCDown', 'C2fCIB', 'ADown', 'AConv', 'RepConv',
                   'RepNCSPELAN4', 'SPPELAN', 'CBLinear', 'CBFuse')
    allowed = (*predictors, *torch_layers, *(getattr(modules, name) for name in yolo_layers),
               block.C3k, block.PSABlock, block.CIB, block.RepVGGDW)
    try:
        # Always use the restricted loader; uploaded checkpoints cannot select their own globals.
        with torch.serialization.safe_globals(list(allowed)):
            checkpoint = torch.load(path, map_location='cpu', weights_only=True)
    except (UnpicklingError, EOFError, RuntimeError, ValueError) as exc:
        raise ValueError('无法读取此 PT 文件，请使用标准 Ultralytics YOLO 训练导出的 best.pt。') from exc

    model = checkpoint
    if isinstance(checkpoint, dict):
        model = checkpoint.get('ema')
        if model is None:
            model = checkpoint.get('model')
    if type(model) not in predictors:
        raise ValueError('请使用 YOLO 目标检测、实例分割或旋转框模型。')
    if any(type(layer) not in allowed for layer in model.modules()):
        raise ValueError('此 PT 使用了当前服务不支持的自定义层。')
    task, predictor_class = predictors[type(model)]
    predictor = predictor_class(overrides={'task': task, 'device': inference_device(), 'verbose': False,
                                          'conf': .25, 'iou': .45, 'save': False, 'imgsz': 640})
    # Pass the checked module itself so prediction does not reopen the checkpoint.
    predictor.setup_model(model=model.float(), verbose=False)
    return predictor


def build_predictor(path):
    if path.suffix == '.onnx':
        return load_onnx(path)
    predictor = load_pt_predictor(path)

    def predict(frame, confidence):
        detections = []
        predictor.args.conf = confidence
        for result in predictor(frame):
            boxes = result.boxes if result.boxes is not None else result.obb
            if boxes is None:
                continue
            for box in boxes:
                index = int(box.cls.item())
                label = str(result.names[index]).strip() or '屋面缺陷'
                detections.append((label[:80], float(box.conf.item()), box.xyxy[0].tolist()))
        return detections

    return predict


@lru_cache(maxsize=2)
def load_predictor(path, signature):
    return build_predictor(path)


def get_predictor(path):
    return load_predictor(path, model_signature(path))


def check_model():
    path = model_path()
    if not model_status()['ready']:
        raise ValueError(model_status()['message'])
    with MODEL_LOCK:
        get_predictor(path)(np.zeros((640, 640, 3), dtype=np.uint8), .25)
