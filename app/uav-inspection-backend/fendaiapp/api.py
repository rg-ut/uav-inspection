"""Serve the optional, prebuilt flower scene without an inference runtime."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from deployment_paths import backend_path
from uav_url_prefix import prefixed

router = APIRouter()


def scene_model_path():
    return backend_path('fendaidata', 'scenes', 'fendai-flower.ksplat')


@router.get('/scene')
async def scene():
    return {'code': 200, 'data': {
        'id': 'fendai-flower',
        'title': '粉黛花漫游',
        'model_url': prefixed('/api/v1/fendai/model'),
        'available': scene_model_path().is_file(),
        'point_count': 1370364,
        'training_iterations': 30000,
    }}


@router.api_route('/model', methods=['GET', 'HEAD'])
async def model():
    path = scene_model_path()
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail='粉黛花场景尚未安装，请在素材上传中选择“粉黛花三维场景（.ksplat）”，上传 fendai-flower.ksplat。',
        )
    return FileResponse(path, media_type='application/octet-stream')
