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
- 病虫害使用玉米三维巡检工作台。区域巡查后生成重点复核队列，再依次规划观察航点、
  飞行、悬停采样与更新观察记录，最后返航。手动优化修改当前航段或后续队列，
  不会通过移动独立相机直接生成观察记录。机载视野来自当前无人机位置。
  暂停、刷新恢复保留正在执行的航段与剩余采样时间；中途返航取消未执行视点，
  保留未完成项目，不把任务完成度改成 100%。
  原有 `pest-data` 分类和旧回放接口仅作为兼容保留。新页面不依赖视频、训练权重或旧模拟 Agent。
  HTML/JSON 报告保存最近 5 个任务，记录实际采样和各视点计划的执行状态。
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

## 玉米病害主动观察演示的任务流程

主界面保留业务语言与演示任务标识；算法参考和工程说明只放在开发文档中。
场景是程序生成的玉米植株与叶片材质；植物线索属于场景配置，不是检测器输出。

1. 全域任务执行区域覆盖；重点任务起飞后直接进入指定样株复核；快速任务默认仅覆盖。
2. 全域巡查后，M01/M02/M03 进入复核队列，M04 仅记为已巡查。重点任务不会把范围外植株标为已巡查。
3. 规划器基于当前无人机位置、目标叶片、遮挡与已采方向选择下一观察位姿。候选为
   12 方位 × 6.2/7.2/8.2 m 三层高度，水平航行先爬升至至少 10 m，以避开玉米冠层。
4. 无人机按新航段飞往视点，抵达后悬停 2 秒。只有悬停结束才生成采样记录；
   位置必须等于该视点实际抵达位置。每株取得 3 个不同有效方向后才复核完成。
5. 遮挡严重时选择其他方向；最多尝试 6 次，仍不足则保留“待复核”，继续处理队列。
6. 手动优化当前植株会取消旧计划、从当前位置重新规划，暂停状态保持不变。
   选择其他植株会加入后续优先队列；待命或粗巡时设置的目标在任务进入复核后执行。
7. 返航和已结束任务不能追加优化。中途返航保留未完成状态与任务完成度，取消未采样计划。

`pestMission.ts` 负责状态与任务执行；Three.js 只提供候选视点几何评估和当前位姿画面。
全景/跟随/俯视只是查看方式；“机载”及右上角机载画面来自无人机当前位姿，不决定采样。
报告中的 `plans` 可区分 planned/arrived/captured/cancelled，`observations.viewId` 关联采样视点，
`arrivedAt/capturedAt` 校验悬停过程。`outcome=interrupted` 表示中途结束。
报告保留 `source=simulation` 和 `disease_detection=null`。

### 参考逻辑

| 参考论文 | 借鉴内容 | 本演示采用的替代 |
|---|---|---|
| Feddock 等，*Active Robotic Perception for Disease Detection and Mapping in Apple Trees*，arXiv:2603.23112 (2026) | 观察—行动—更新、语义与几何视点选择 | 玉米叶片几何可见性、线索配置与实际飞行采样记录 |
| Chen 等，*GenNBV: Generalizable Next-Best-View Policy for Active 3D Reconstruction*，arXiv:2402.16174 (2024) | 位姿选择、观察历史与覆盖增益 | 三层高度候选、方向历史、启发式排序；未训练强化学习策略 |
| van Essen 等，*Adaptive path planning for efficient object search by UAVs in agricultural fields*，Smart Agricultural Technology 12 (2025) 101075，doi:10.1016/j.atech.2025.101075 | 高空覆盖后低空重点检查，融合观察并优化检查路径 | 覆盖阶段、重点样株队列、逐次生成安全观察航段 |

演示没有实现真实病害模型、语义体素重建、真实无人机控制或论文的性能复现。
三篇研究分别针对苹果树、通用重建、农业目标搜索，不能把它们描述为已实现的玉米识别模型。
