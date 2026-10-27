# 业务模块缺项诊断与恢复

平台能打开、粉黛花场景能漫游，只说明基础服务与场景文件可用。巡检识别另外依赖
业务视频、对应训练权重和推理运行环境；测量工作台另外依赖预处理点云与结果。
这些数据不在代码仓库和默认镜像中，不会随合并 PR 自动出现。

## 查看当前缺项

访问 `GET /api/deployment/status`（子路径部署为 `/uav/api/deployment/status`）。
新增 `data.modules` 按业务分别列出：

- `mode`：AI 推理、测量、离线结果、演示或场景。
- `configured`：当前模块所需文件与依赖包是否齐备。
- `missing`：缺项说明、上传分类和建议子目录；页面可直接跳转上传。
- `note`：模块边界与后续验证方式。

`configured` 仅检查文件存在和依赖包可发现，**不代表权重已成功加载或识别准确**。
上传损坏/不匹配权重时，仍应查看模块 `/health` 和实际 `/detect` 的响应。
保留的 `data_available`、`inference_available` 是全局兼容字段，不能用来判断每个模块。
例如只装入热力模型就可能使全局模型标志为真，通信基站仍可能缺模型。

## 安装真实推理环境

默认镜像保持基础模式。需要检测时，从仓库根目录构建包含 CPU 推理依赖的镜像：

```bash
docker build -f deploy/docker/Dockerfile \
  --build-arg VITE_BASE=/uav/ \
  --build-arg UAV_INSTALL_INFERENCE=1 \
  -t uav-inspection:inference .
```

在 `deploy/.env` 设置 `UAV_IMAGE=uav-inspection:inference`，再执行：

```bash
docker compose -f deploy/compose.yml up -d
```

通过已有 registry 流程部署时：

```bash
UAV_INSTALL_INFERENCE=1 VITE_BASE=/uav/ bash deploy/scripts/build_and_push.sh
```

CPU 镜像不要求 GPU，性能取决于模型和视频大小。GPU 环境应自行安装匹配的 Torch/CUDA。
本地虚拟环境可先安装基础依赖，再安装 `requirements-inference.txt`。
镜像构建不会提供或下载业务训练权重；仅上传权重也不会为正在运行的容器安装依赖。

Dockerfile 默认前端基路径已与 Compose 的 `/uav` 一致。根路径部署须显式设置
`VITE_BASE=/` 并将 `UAV_URL_PREFIX` 置空；反向代理仍应按原配置剥除前缀。

## 上传对应业务资产

专用模型分类限定文件名、要求子目录留空，普通上传和分片上传使用相同校验。
上传可信的训练模型；不要把它们传进“其他模型”，该分类主要供测量模型使用。

| 业务 | 模型分类 / 文件名 | 视频与其他数据 |
|---|---|---|
| 通信基站 | `telecom-model` / `station2-best.pt` | 页面创建视频 + SRT 任务；或 `telecom-tasks` 下同一任务子目录上传视频和字幕 |
| 杆路线路 | `power-model` / `wire-pole-seg.pt` | `power-media`，子目录留空 |
| 乔木修剪 | `pruning-model` / `tree-pruning-best.pt` | `pruning-media`，子目录留空 |
| 外墙 AI 检测 | `wall-model` / `wall-damage-best.pt` | `wall-media`，子目录留空；缺模型时原规则检测仍可使用 |
| 玫瑰产量 | `rose-model` / `rose-detect-best.pt` | 页面创建视频 + SRT 任务；或 `rose-tasks` 下同一任务子目录上传视频和字幕 |
| 热力客流 | `heatmap-model` / `renliu.pt` | `heatmap-videos`，同名 MP4 / SRT 放根目录 |

操作时进入首页 → 素材上传中心 → 选择对应的专用模型分类 → 子目录留空 →
选择表中固定文件名的 `.pt` → 开始上传。大文件会自动分片，暂停后可继续；
完成后回到模块重新检查。默认不覆盖重名文件，确认要替换时再开启覆盖。
病虫害尚未接入实际检测器，没有可启用识别的模型上传分类；把 `.pt` 传到“其他模型”
不会使其自动识别。页面已去除写死的病害框与置信度，改为 Three.js 仿真，无需视频。

玫瑰仍支持 `ROSE_MODEL_PATH` 和旧文件名 `best.pt`。部署状态与检测器使用同一查找规则。
覆盖已加载的模型后应重启服务，以释放检测器缓存；首次补入缺失模型后可重新检查。

### 树高与绿化测量

使用后端预定义的任务编号，例如 `task_01_xinxixueyuan_1`，保持所有资产编号一致。
不是把任意视频或 PLY 上传后就能直接得到树高：这些模块消费已有处理结果。

| 分类 | 子目录 | 文件 |
|---|---|---|
| `tree-instances` | 任务编号 | `summary.json`、`tree_instances.csv`、可选 `tree_instances_overview.png` |
| `measurement-data` | 同一任务编号 | `shrub_summary.json`、`green_area_summary.json`、已有预览图/掩膜 |
| `measurement-data` | `pointcloud_web/任务编号` | `scene.json`、`scene.ply` |
| `models` | `green_area_gnb_v1` | `model.json`，可选既有 `metrics.json`、`confusion_matrix.png` |
| `models` | `tree_shrub_gnb_v1` | 可选既有 `metrics.json`、`confusion_matrix.png` |

新增 `tree-instances` 分类写入后端实际读取的 `prelabel_output/instances_20260715`。
此前只上传 measurement_data 无法满足 overview 的联合检查，任务会一直为空。
没有原始数据时，需按 `app/scripts/` 的既有处理流程生成这些文件；不要用空 JSON
替代真实测量数据。配置检查不校验业务 JSON/PLY/CSV 的内容正确性。

### 屋顶、病虫害与数字玫瑰园

- 屋顶当前**没有实时推理接口**：页面读取离线生成的缺陷、轨迹与报告。
  `roof-media/任务编号` 放视频与可选字幕，`roof-reports/同一任务编号` 放
  `defects.json`、`telemetry.json` 和可选 `report.html`。单独上传视频不会生成检测结果。
- 病虫害页面改为 Three.js 三维仿真：支持三条航线、开始/暂停/继续、安全返航、
  玉米粗巡与精查、视角切换、36 个候选视点的几何遮挡与方向增益比较、巡检助手、HTML/JSON 报告与本地恢复。
  仿真位置、航程、时间、电量和日志随运行更新，暂停后保持不变。刷新会恢复为暂停。
  隐藏页面会自动暂停，防止后台继续飞行。真实无人机、病害检测器、病害统计均未接入。
  原有 `pest-data` 上传分类和后端回放接口仅作为兼容保留，新页面不再使用视频、
  静态病害框、伪造置信度、旧模拟 Agent 或旧报告生成接口。
  仿真报告在当前浏览器保存最近 5 个任务，下载文件明确标注仿真来源。
- 数字玫瑰园展示内置示例地块，不依赖推理安装。页面提示其演示性质。
- 粉黛花保持独立场景加载，无需 Torch/YOLO。

## 子路径视频地址

API 返回的视频 URL 可能已经含 `/uav`。前端统一使用幂等的 `webPath`，
玫瑰任务上传后不会再生成 `/uav/uav/api/...`；根路径、外部 URL 与临时 Blob 地址仍可用。

## 验证

后端测试需按文件分别启动进程，避免 `UAV_DATA_DIR` 的模块级缓存互相污染：

```bash
cd app/uav-inspection-backend
python -m unittest discover -s tests -p test_module_readiness.py
python -m unittest discover -s tests -p test_optional_deployment.py
python -m unittest discover -s tests -p test_uav_assets_upload.py
python -m unittest discover -s tests -p test_uav_assets_chunked.py
python -m unittest discover -s tests -p test_heatmap_srt_parser.py
cd ../uav-inspection-ui
node --test tests/*.test.mjs
npm run build
VITE_BASE=/uav/ npm run build
```

安装依赖和真实资产后，还需播放实际视频、发起实际检测并校验结果。配置测试使用临时
文件验证路径与协议，不替代训练模型的加载、推理质量验收或服务器镜像构建验收。

## 玉米病害主动观察演示：论文借鉴与实现边界

本次按三篇用户提供论文的逻辑制作交互演示，没有复制论文图片、模型、训练数据或性能结论。
玉米模型用程序几何生成；叶片材质与线索值属于演示场景配置，不构成植物病理诊断。主界面只保留业务流程及演示任务标识，算法参考与工程说明保留在本开发文档中。

| 参考论文 | 借鉴逻辑 | 本项目演示 | 未实现部分 |
|---|---|---|---|
| Feddock 等，*Active Robotic Perception for Disease Detection and Mapping in Apple Trees*，arXiv:2603.23112 (2026) | 观察—行动—更新；几何与语义不确定性视点选择 | 玉米样株观察记录；固定、覆盖优先、语义线索优先三种机位策略 | 真实疾病实例分割、立体深度、语义 OctoMap、机械臂执行 |
| Chen 等，*GenNBV: Generalizable Next-Best-View Policy for Active 3D Reconstruction*，arXiv:2402.16174 (2024) | 联合几何、语义、历史观察，选择位置与相机方向，追求覆盖增益 | 3 层高度 × 12 方位共 36 个候选，相机朝向目标；保存位置、yaw/pitch、可见比例及方向历史 | 强化学习策略、训练及泛化验证、真实三态体素地图与 3D 重建 |
| van Essen 等，*Adaptive path planning for efficient object search by UAVs in agricultural fields*，Smart Agricultural Technology 12 (2025) 101075，doi:10.1016/j.atech.2025.101075 | 高空粗巡后，仅低空复查不确定目标；合并观察、缩短复查路径 | 14 m 粗巡；按预设 0.2/0.8 阈值选择 M01/M02，在 5.5 m 各取 3 个方向；简单近邻排序；与仅高空/单株环绕航线对照 | 真正的在线检测、地理配准、检测融合、最优路径与论文实验复现 |

演示流程：选择“一号试验田 · 全域巡检”并起飞，粗巡结束后状态自动进入低空精查，
到达精查航点时由场景射线计算目标可见比例。每株有 8 个观察方向分箱；可见比例至少 50%
才计入覆盖，同一方向重复观察不重复增加。它是观察方向覆盖，**不是病害识别率或完整表面重建率**。
NBV 按钮移动独立观察相机，巡航无人机沿精查航线飞行，两者在 UI 中明确区分。
视点排序是确定性启发式，未调用 GenNBV 的训练策略；语义线索来自预设场景，非网络输出。
报告包含实际本次仿真轨迹、可见性观察、候选视点位姿，`source=simulation`、`disease_detection=null`。

三篇原研究分别针对苹果树、通用物体重建、农业目标搜索。本演示仅把流程迁移到玉米示意场景，
不能据此声称原论文提出了玉米病害模型，也不使用其指标作为本项目结果。
