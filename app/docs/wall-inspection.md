# 外墙巡检配置

外墙入口为 /wall。原有任务通过“历史任务”进入。新任务、工单和分析记录保存在 UAV_DATA_DIR 下的 uav-inspection-backend/walldata/workflow。备份时保留整个目录，包含 SQLite 文件、媒体和报告。首期使用单个 Uvicorn 服务进程。

## 模型

工作台支持 640×640、单类别、未导出 NMS 的 YOLO 检测 ONNX，输出布局为 [1, 5, N]。单类别应代表外墙脱落，所有登录账号均可上传。已有 .pt 权重须来自可信来源，放在部署状态说明指定位置，并安装可选推理依赖。录像任务固定模型文件版本。无模型时，可导入录像、查看飞行数据和现有记录。

UAV_WALL_ADMINS 是以逗号分隔的管理员账号，默认没有外墙管理员；现在仅用于 UAV_WALL_IMPORT_DIR 指定的原有本地路径导入，只有管理员可使用该导入方式。UAV_WALL_MAX_VIDEO_BYTES 默认 8 GiB。UAV_WALL_FFPROBE、UAV_WALL_FFMPEG 可指定程序路径。

## 现场直播

媒体网关由现场部署提供。应用进程需要能访问内部 WHEP 地址。浏览器需要能访问网关提供的 WebRTC ICE 地址。将网关的 WHEP HTTP 入口限制在内部，使用发布凭据控制推流。应用按设备名单和已登录账号代理信令。

UAV_WALL_LIVE_DEVICES 是 JSON 对象。每项可设 name、whep、authorization 和 users。示例：

    {"m4t":{"name":"Matrice 4T","whep":"http://media:8889/m4t/whep","users":["pilot"]},"mini":{"name":"Mini 5 Pro","whep":"http://media:8889/mini/whep","users":["pilot"]}}

- Matrice 4T 通过既有 Pilot 2 上云项目向网关推流。按遥控器实际上报能力配置。已具备接入和绑定的项目可运行 app/scripts/wall_dji_bridge.py 来转发飞行数据。桥接程序依赖 paho-mqtt 2.x，订阅经 TLS 认证的 broker，按 UAV_WALL_DJI_SERIALS 指定的飞行器序列号订阅 OSD。设置 UAV_WALL_MQTT_HOST、UAV_WALL_MQTT_USER、UAV_WALL_MQTT_PASSWORD；可设置 UAV_WALL_MQTT_PORT 和 UAV_WALL_MQTT_CA。UAV_WALL_BRIDGE_API 指向 /api/v1/wall/workflow（含部署前缀），并和平台使用同一 UAV_WALL_TELEMETRY_KEY。
- Mini 5 Pro 用 DJI Fly 手动向现场网关推 RTMP。用实际遥控器与固件验证支持情况；有录像和 SRT 时回到工作台上传。
- 直播预览检测由浏览器抽取最新画面，每次只处理一帧。直播与录像任务分开运行。现场可用网关录制并将原片导入工作台复核。

## 巡检助手

助手内置常用问法和动作。UAV_WALL_ASSISTANT_URL 可设为 OpenAI 兼容的聊天完成端点，并配置 UAV_WALL_ASSISTANT_MODEL 和可选的 UAV_WALL_ASSISTANT_KEY。只在内置流程未能解析问句时发送用户输入，不发送任务媒体、地图或工单内容。工单仍由用户预览后确认。
