"""Explicit inference smoke checks; uploading a file alone never means ready."""
import time
from threading import Lock

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from authapp.api import require_account
from deployment_readiness import MODEL_PATHS, module_status
from optional_inference import inference_dependencies_available

router = APIRouter()
check_lock = Lock()


@router.post('/check/{module}')
def check_module(module: str, account=Depends(require_account)):
    status = module_status().get(module)
    if status is None:
        raise HTTPException(404, '未知巡检模块')
    if status['mode'] != 'inference':
        return {'code': 200, 'data': {**status, 'model_ready': None, 'message': status['note']}}
    if not check_lock.acquire(blocking=False):
        raise HTTPException(429, '其他模型正在检查，请稍后重试')
    started = time.perf_counter()
    try:
        frame = np.zeros((640, 640, 3), dtype=np.uint8)
        if module == 'wall':
            from wallapp import analysis
            from wallapp.api.workflow import list_models, preview_predictor, _preview_lock
            options = list_models(account)['data']['models']
            if not options:
                raise ValueError('尚未添加外墙检测模型，请进入外墙工作台添加 ONNX，或在素材中心上传可信 PT 权重。')
            # Prefer a CPU ONNX model when the optional PT runtime is absent.
            version = next((item['id'] for item in options if item['id'] != 'installed'), options[0]['id'])
            path = analysis.model_path(version)
            if path.suffix == '.pt' and not inference_dependencies_available():
                raise ValueError('PT 权重已保存，但服务器尚未安装 requirements-inference.txt；也可使用 ONNX 模型进行 CPU 检测。')
            cache_key = version + ':' + str(path.stat().st_mtime_ns)
            with _preview_lock:
                preview_predictor(cache_key)(frame, 0.25, 0.45)
        elif module == 'roof':
            from roofapp.runtime import check_model
            check_model()
        else:
            path = MODEL_PATHS[module]()
            if not path.is_file():
                raise ValueError('尚未上传该模块的训练权重。')
            if not inference_dependencies_available():
                raise ValueError('权重已保存，但服务器尚未安装 requirements-inference.txt。请在服务器执行 python deploy.py install --backend-only --inference，完成后重新检查。')
            if module == 'heatmap':
                from heatmapapp.services.ai_inference import engine
                engine.ensure_model()
                with engine._inference_lock:
                    engine.model.predict(frame, verbose=False, device='cpu')
            else:
                if module == 'telecom':
                    from telecomapp.detector import telecom_detector as detector
                elif module == 'power':
                    from powerapp.detector import power_detector as detector
                elif module == 'pruning':
                    from treeapp.detector import tree_pruning_detector as detector
                else:
                    from roseapp.detector import rose_detector as detector
                detector.detect(frame)
        return {'code': 200, 'data': {**status, 'model_ready': True,
                'elapsed_ms': round((time.perf_counter() - started) * 1000),
                'message': '模型已加载并完成一帧真实试运行。' + ('请进入模块选择视频并开始检测。' if status['configured'] else '仍需补齐下方素材。')}}
    except Exception as exc:
        # Expected errors contain actionable installation details; server trace stays in logs.
        from optional_inference import InferenceUnavailableError
        message = str(exc) if isinstance(exc, (ValueError, InferenceUnavailableError)) else '模型试运行失败，请检查权重格式、类别和推理环境，详细原因见服务器日志。'
        import logging
        logging.getLogger(__name__).warning('Module %s smoke check failed: %s', module, exc)
        return {'code': 200, 'data': {**status, 'model_ready': False, 'message': message}}
    finally:
        check_lock.release()
