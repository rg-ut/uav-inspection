"""Report module prerequisites without loading models or changing runtime data.

Configured files are not proof that a trained model can be loaded. Module health
and detection endpoints remain the authority for actual inference readiness.
"""
from __future__ import annotations

from pathlib import Path

from deployment_paths import backend_path
from optional_inference import inference_dependencies_available
from roseapp.detector import _resolve_model_path
from roofapp import runtime as roof_runtime
from vegetationapp import api as vegetation

VIDEO_SUFFIXES = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
MODEL_PATHS = {
    'heatmap': lambda: backend_path('heatmapweight', 'renliu.pt'),
    'rose': _resolve_model_path,
    'telecom': lambda: backend_path('telecomapp', 'station2-best.pt'),
    'power': lambda: backend_path('powerapp', 'wire-pole-seg.pt'),
    'wall': lambda: backend_path('wallapp', 'wall-damage-best.pt'),
    'roof': roof_runtime.model_path,
    'pruning': lambda: backend_path('treeapp', 'tree-pruning-best.pt'),
}


def _has_video(root: Path, recursive: bool = False) -> bool:
    files = root.rglob('*') if recursive else root.glob('*')
    return any(p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES for p in files)


def _paired_task(root: Path) -> bool:
    return any(folder.is_dir() and _has_video(folder)
               for folder in root.glob('*'))


def module_status() -> dict[str, dict]:
    dependencies = inference_dependencies_available()
    modules: dict[str, dict] = {}

    def entry(key: str, title: str, route: str, mode: str, missing: list[dict], note: str = ''):
        modules[key] = {
            'title': title, 'route': route, 'mode': mode,
            'configured': not missing, 'missing': missing, 'note': note,
        }

    def requirement(label: str, category: str = '', subdir: str = '') -> dict:
        return {'label': label, 'category': category, 'subdir': subdir}

    for key, title, route, media_ready, media_category in (
        ('telecom', '通信基站巡检', '/telecom', _paired_task(backend_path('telecom-tasks'))
         or _has_video(backend_path('telecomdata', 'media')), 'telecom-tasks'),
        ('power', '杆路线路巡检', '/power', _has_video(backend_path('powerdata', 'media')), 'power-media'),
        ('pruning', '乔木修剪检测', '/pruning', _has_video(backend_path('treedata', 'media')), 'pruning-media'),
        ('rose', '玫瑰产量估计', '/rose-yield', _paired_task(backend_path('rose-tasks')), 'rose-tasks'),
        ('heatmap', '热力客流监测', '/heatmap-flow', _has_video(backend_path('heatmapdata', 'videos')), 'heatmap-videos'),
    ):
        missing = []
        if not media_ready:
            missing.append(requirement('巡检视频', media_category))
        if not MODEL_PATHS[key]().is_file():
            missing.append(requirement(f'训练模型 {MODEL_PATHS[key]().name}', f'{key}-model'))
        if not dependencies:
            missing.append(requirement('服务器 AI 推理依赖（构建时启用 UAV_INSTALL_INFERENCE=1）'))
        note = '视频和模型齐备后，请试运行模型；字幕可选，仅定位和飞行数据需要对应字幕。'
        if key == 'wall':
            note = '缺少 AI 模型时仍可使用规则检测；规则结果不等同于 AI 识别。'
        entry(key, title, route, 'inference', missing, note)

    from wallapp import analysis
    wall_missing = []
    # Task membership is account-scoped in the workbench; this overview only checks files.
    if not _has_video(analysis.ROOT / 'media', recursive=True):
        wall_missing.append(requirement('在外墙工作台上传巡检任务视频', 'wall-media'))
    has_onnx = any(path.with_suffix('.json').is_file() for path in analysis.MODEL_DIR.glob('*.onnx'))
    if not has_onnx and not MODEL_PATHS['wall']().is_file():
        wall_missing.append(requirement('外墙 ONNX 模型或可信 PT 权重', 'wall-model'))
    if not has_onnx and not dependencies:
        wall_missing.append(requirement('PT 权重需要服务器 AI 推理依赖，或改用 ONNX'))
    entry('wall', '建筑外墙巡检', '/wall', 'inference', wall_missing,
          '新版工作台使用账号绑定任务；ONNX 可直接在 CPU 运行，PT 需要额外推理依赖。')

    roof_tasks = backend_path('roofdata', 'media')
    roof_ready = any(folder.is_dir() and _has_video(folder) and all(
        backend_path('roofdata', 'reports', folder.name, name).is_file()
        for name in ('defects.json', 'telemetry.json')) for folder in roof_tasks.glob('*'))
    roof_model = roof_runtime.model_path()
    if roof_ready and not roof_model.is_file():
        entry('roof', '建筑屋顶巡检', '/roof', 'offline-results', [],
              '可查看历史检测结果。添加屋顶模型后，可检测新任务。')
    else:
        roof_missing = []
        if not _paired_task(roof_tasks):
            roof_missing.append(requirement('在屋顶巡检页上传任务录像'))
        if not roof_model.is_file():
            roof_missing.append(requirement('在屋顶巡检页上传 YOLO 权重'))
        elif roof_model.suffix == '.pt' and not dependencies:
            roof_missing.append(requirement('安装服务器 AI 推理依赖，或上传兼容的 ONNX 权重'))
        entry('roof', '建筑屋顶巡检', '/roof', 'inference', roof_missing,
              '上传录像和对应的可选 SRT 字幕，添加模型后开始检测。')

    task_key = vegetation.TASKS[0]['key']
    summaries_ready = any(all(path.is_file() for path in (
        vegetation.INSTANCE_ROOT / task['key'] / 'summary.json',
        vegetation.INSTANCE_ROOT / task['key'] / 'tree_instances.csv',
        vegetation.MEASUREMENT_ROOT / task['key'] / 'shrub_summary.json',
        vegetation.MEASUREMENT_ROOT / task['key'] / 'green_area_summary.json',
    )) for task in vegetation.TASKS)
    scenes_ready = any(all(path.is_file() for path in (
        vegetation.INSTANCE_ROOT / task['key'] / 'summary.json',
        vegetation.INSTANCE_ROOT / task['key'] / 'tree_instances.csv',
        vegetation.MEASUREMENT_ROOT / task['key'] / 'shrub_summary.json',
        vegetation.MEASUREMENT_ROOT / task['key'] / 'green_area_summary.json',
        vegetation.WEB_POINTCLOUD_ROOT / task['key'] / 'scene.json',
        vegetation.WEB_POINTCLOUD_ROOT / task['key'] / 'scene.ply',
    )) for task in vegetation.TASKS)
    for key, title in (('height', '树高测量'), ('area', '绿化面积测量')):
        missing = []
        if not summaries_ready:
            missing.extend([
                requirement('原始任务的乔木实例 summary.json / tree_instances.csv', 'tree-instances', task_key),
                requirement('同一任务的 shrub_summary.json / green_area_summary.json', 'measurement-data', task_key),
            ])
        if not scenes_ready:
            missing.append(requirement('同一任务的点云 scene.json / scene.ply', 'measurement-data', f'pointcloud_web/{task_key}'))
        if not (vegetation.AREA_MODEL_DIR / 'model.json').is_file():
            missing.append(requirement('点云分析模型 green_area_gnb_v1/model.json', 'models', 'green_area_gnb_v1'))
        entry(key, title, f'/{key}', 'measurement', missing,
              '测量基于已有点云与测量数据，不需要安装 YOLO；各文件的任务编号必须一致。')

    entry('pest', '病虫害巡检', '/pest', 'simulation', [],
          '玉米单株、两株与三株密植的仿真记录回放，支持三种巡检方式对比、相机画面与 JSON 导出；无需额外推理依赖。')
    entry('rose-digital', '数字玫瑰园', '/rose-digital', 'demo', [], '页面展示内置示例地块数据。')
    scene_ready = backend_path('fendaidata', 'scenes', 'fendai-flower.ksplat').is_file()
    entry('fendai', '粉黛花漫游', '/fendai-flower', 'scene', [] if scene_ready else [
        requirement('fendai-flower.ksplat 场景', 'fendai-scenes')])
    return modules
