# 粉黛花三维漫游部署

首页的“粉黛花三维漫游”打开 `/fendai-flower`。浏览器渲染预生成的高斯场景，
后端提供场景元数据和模型文件。代码与约 99 MB 的模型分别发布，未安装模型也能启动平台。

## 安装场景

下载 [场景 Release](https://github.com/rg-ut/uav-inspection/releases/tag/fendai-scene-v1)
中的 `fendai-flower.ksplat`，或者在仓库根目录运行：

```bash
python app/scripts/install_fendai_scene.py
```

默认安装到 `app/uav-inspection-backend/fendaidata/scenes/fendai-flower.ksplat`。
服务使用外置数据时，安装命令的 `--data-dir` 必须对应服务的 `UAV_DATA_DIR`：

```bash
python app/scripts/install_fendai_scene.py --data-dir /srv/uav-data
```

已下载模型可离线安装：

```bash
python app/scripts/install_fendai_scene.py --source /path/to/fendai-flower.ksplat --data-dir /srv/uav-data
```

安装器在落盘前检查大小与 SHA-256，匹配的已安装模型会直接复用；不同的已有文件默认拒绝覆盖。
先备份、确认需要替换后才使用 `--overwrite`。安装器支持 Python 3.10–3.13，使用标准库。
轻量交付包中的命令为 `python scripts/install_fendai_scene.py`，默认遵循 `deployment-layout.json` 指向的 `02-data`。

也可以打开平台“素材上传中心”，选择“粉黛花三维场景（.ksplat）”，子目录留空，
上传文件名为 `fendai-flower.ksplat` 的模型。数据目录为：

```text
UAV_DATA_DIR/
└── uav-inspection-backend/
    └── fendaidata/
        └── scenes/
            └── fendai-flower.ksplat
```

素材中心沿用既有分片上传和防覆盖机制。上传完成后重新进入漫游页，无需重建前端。
“其他模型”分类写入不同目录，不能替代该专用分类。页面的点数与训练轮次描述此发布模型；
自定义模型还需要同步更新 `fendaiapp/api.py` 元数据、场景变换和初始相机。

## Docker 与子路径

Compose 已将 `/data` 挂为持久化卷。启动服务后，浏览器上传是最简单的模型安装方式。
也可在仓库根目录用本地安装器校验文件，再复制到运行容器的数据卷：

```bash
python app/scripts/install_fendai_scene.py
docker compose -f deploy/compose.yml exec uav mkdir -p /data/uav-inspection-backend/fendaidata/scenes
docker compose -f deploy/compose.yml cp app/uav-inspection-backend/fendaidata/scenes/fendai-flower.ksplat uav:/data/uav-inspection-backend/fendaidata/scenes/fendai-flower.ksplat
```

构建新增代码对应的镜像时，根路径使用 `VITE_BASE=/` 与空的 `UAV_URL_PREFIX`；
挂到 `/uav/` 时使用 `VITE_BASE=/uav/`、`UAV_URL_PREFIX=/uav` 和剥前缀的 Nginx 转发。
从仓库根目录可构建子路径镜像：

```bash
docker build --build-arg VITE_BASE=/uav/ -f deploy/docker/Dockerfile -t uav-inspection:fendai .
```

将 Compose 的 `UAV_IMAGE` 设置为该镜像再启动。模型由卷提供，`.dockerignore` 排除本地模型。
新页面已加入后端 SPA 白名单，直接访问和刷新 `/fendai-flower` 均可返回前端。
API 响应中的模型 URL 已含部署前缀，前端直接使用。

## 接口与校验

| 接口 | 用途 |
|---|---|
| `GET /api/v1/fendai/scene` | 返回模型是否安装、带前缀模型 URL、点数与训练轮次 |
| `GET /api/v1/fendai/model` | 返回固定 KSplat 文件，支持 Range；缺失为 404 |
| `HEAD /api/v1/fendai/model` | 查询文件大小等响应头 |
| `GET /api/deployment/status` | `fendai_scene_available` 表示场景文件存在 |

接口仅提供指定场景文件，不公开整个模型目录。固定元数据并非实时解析任意上传文件。
发布校验信息也保存在 [fendai-scene.json](fendai-scene.json)：

```text
文件：fendai-flower.ksplat
大小：98,735,796 字节
高斯点：1,370,364
格式：KSplat 0.1，压缩级别 1
训练：30,000 iterations
SHA-256：101b60a7033cbf7d740c74b33d8be3f9ce358134e6de6521a03e7d42c0ce32f8
```

`scripts/build_delivery.py` 将已安装的 `fendaidata/scenes` 收入可选数据包，
`--without-data` 仅交付代码。训练目录不进入数据包。

## 操作与验证

桌面点击“进入第一人称漫游”后用鼠标转向，WASD 移动，Space/Ctrl 升降，Shift 加速，
R 重置；Esc 退出鼠标锁定。手机拖动画面转向，使用方向和升降按钮。
页面提供重置、全屏、加载与失败状态，退出页面会停止下载、动画与输入监听并释放渲染资源。

模型下载后仍需解析和图形内存，低性能设备应实测。该场景是自由漫游，没有碰撞地形、
实测尺寸、花量预测或病虫害识别。服务端展示无需训练环境，客户端需要能运行 WebGL 的浏览器。
当前关闭共享内存与 GPU 排序，无需为此功能增加 COOP/COEP；已有严格 CSP 的站点需允许库使用的 Blob Worker/WASM。

验证命令：

```bash
cd app/uav-inspection-ui
node --test tests/webroot.test.mjs
npm run build
# 子路径构建（PowerShell: $env:VITE_BASE='/uav/'; npm run build）
VITE_BASE=/uav/ npm run build
cd ../uav-inspection-backend
python -m unittest discover -s tests -p test_optional_deployment.py -v
python -m unittest discover -s tests -p test_uav_assets_upload.py -v
python -m unittest discover -s tests -p test_uav_assets_chunked.py -v
cd ..
python -m unittest discover -s tests -v
```

浏览器验收应覆盖根/子路径的直接访问和刷新、模型缺失指引、真实模型加载、
重置与返回首页、手机触控、反复进入退出，以及加载过程中返回首页。
