"""Serve the optional flower scene without an inference runtime."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from deployment_paths import backend_path
from fendaiapp.garden import GARDEN, prepare_garden_model
from uav_url_prefix import prefixed

router = APIRouter()


def scene_model_path():
    return backend_path('fendaidata', 'scenes', 'fendai-flower.ksplat')


@router.get('/scene')
def scene():
    path = scene_model_path()
    garden = prepare_garden_model(path)
    data = {
        'id': 'fendai-flower',
        'title': '粉黛花漫游',
        'model_url': prefixed('/api/v1/fendai/model'),
        'available': path.is_file(),
        'point_count': 1370364,
        'training_iterations': 30000,
    }
    if garden:
        _, count = garden
        data.update(
            model_url=prefixed('/api/v1/fendai/model') + '?garden=v1',
            point_count=count,
            garden={key: value for key, value in GARDEN.items() if key not in {
                'crop', 'modelBytes', 'modelSha256', 'pointCount',
            }},
        )
    return {'code': 200, 'data': data}


@router.api_route('/model', methods=['GET', 'HEAD'])
def model(garden: str | None = None):
    path = scene_model_path()
    if garden == 'v1':
        optimized = prepare_garden_model(path)
        if not optimized or not optimized[0].is_file():
            raise HTTPException(status_code=404, detail='粉黛花精剪场景暂不可用，请刷新漫游页。')
        path = optimized[0]
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail='粉黛花场景尚未安装，请在素材上传中选择“粉黛花三维场景（.ksplat）”，上传 fendai-flower.ksplat。',
        )
    return FileResponse(path, media_type='application/octet-stream')
