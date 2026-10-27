"""Report module prerequisites without loading models or changing runtime data.

Configured files are not proof that a trained model can be loaded. Module health
and detection endpoints remain the authority for actual inference readiness.
"""
from __future__ import annotations

from pathlib import Path

from deployment_paths import backend_path
from optional_inference import inference_dependencies_available
from roseapp.detector import _resolve_model_path
from vegetationapp import api as vegetation

VIDEO_SUFFIXES = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
MODEL_PATHS = {
    'heatmap': lambda: backend_path('heatmapweight', 'renliu.pt'),
    'rose': _resolve_model_path,
    'telecom': lambda: backend_path('telecomapp', 'station2-best.pt'),
    'power': lambda: backend_path('powerapp', 'wire-pole-seg.pt'),
    'wall': lambda: backend_path('wallapp', 'wall-damage-best.pt'),
    'pruning': lambda: backend_path('treeapp', 'tree-pruning-best.pt'),
}


def _has_video(root: Path, recursive: bool = False) -> bool:
    files = root.rglob('*') if recursive else root.glob('*')
    return any(p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES for p in files)


def _paired_task(root: Path) -> bool:
    return any(folder.is_dir() and _has_video(folder) and
               any(p.is_file() and p.suffix.lower() in {'.srt', '.txt', '.vtt'} for p in folder.iterdir())
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
        ('telecom', '通信基站巡检', '/telecom', _paired_task(backend_path('telecom-tasks')), 'telecom-tasks'),
        ('power', '杆路线路巡检', '/power', _has_video(backend_path('powerdata', 'media')), 'power-media'),
        ('pruning', '乔木修剪检测', '/pruning', _has_video(backend_path('treedata', 'media')), 'pruning-media'),
        ('wall', '建筑外墙巡检', '/wall', _has_video(backend_path('walldata', 'media')), 'wall-media'),
        ('rose', '玫瑰产量估计', '/rose-yield', _paired_task(backend_path('rose-tasks')), 'rose-tasks'),
        ('heatmap', '热力客流监测', '/heatmap-flow', _has_video(backend_path('heatmapdata', 'videos')), 'heatmap-videos'),
    ):
        missing = []
        if not media_ready:
            missing.append(requirement('巡检视频与对应字幕' if key in {'telecom', 'rose'} else '巡检视频', media_category))
        if not MODEL_PATHS[key]().is_file():
            missing.append(requirement(f'训练模型 {MODEL_PATHS[key]().name}', f'{key}-model'))
        if not dependencies:
            missing.append(requirement('服务器 AI 推理依赖（构建时启用 UAV_INSTALL_INFERENCE=1）'))
        note = '文件齐备后，请在模块中检查模型能否加载。'
        if key == 'wall':
            note = '缺少 AI 模型时仍可使用规则检测；规则结果不等同于 AI 识别。'
        entry(key, title, route, 'inference', missing, note)

    roof_tasks = backend_path('roofdata', 'media')
    roof_ready = any(folder.is_dir() and _has_video(folder) and all(
        backend_path('roofdata', 'reports', folder.name, name).is_file()
        for name in ('defects.json', 'telemetry.json')) for folder in roof_tasks.glob('*'))
    roof_missing = [] if roof_ready else [requirement('同一任务的视频、defects.json 与 telemetry.json', 'roof-reports')]
    entry('roof', '建筑屋顶巡检', '/roof', 'offline-results', roof_missing,
          '当前模块回放离线检测结果；上传新视频不会自动生成缺陷与报告。')

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

    pest_missing = [] if _has_video(backend_path('pestdata', 'media')) else [requirement('病虫害回放视频', 'pest-data', 'media')]
    entry('pest', '病虫害巡检', '/pest', 'demo', pest_missing,
          '航线、模型切换和 Agent 为演示；未接入真实无人机或病虫害推理服务。')
    entry('rose-digital', '数字玫瑰园', '/rose-digital', 'demo', [], '页面展示内置示例地块数据。')
    scene_ready = backend_path('fendaidata', 'scenes', 'fendai-flower.ksplat').is_file()
    entry('fendai', '粉黛花漫游', '/fendai-flower', 'scene', [] if scene_ready else [
        requirement('fendai-flower.ksplat 场景', 'fendai-scenes')])
    return modules
