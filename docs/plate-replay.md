# 无人机车牌巡检回放

平台首页的「无人机车牌巡检」进入 /plate。页面以全景视频为主时钟，根据各视频记录起点的偏移量同步机上画面与拍照位置。第四格根据任务日志中的视点和时间绘制路线，关键事件随任务时间更新。

## 数据包

新批次通过平台数据目录交付。在 UAV_DATA_DIR 下安装完整的 `uav-inspection-backend/platedata/`，包含 timeline.json、coverage.json、acceptance.json、cards/ 和 media/。media/ 中包含主视频、RViz 录屏、path.json 和页面引用的封面。安装后重启后端以加载新批次。未配置新批次时，页面读取仓库附带的旧批次，只有提供相应素材时才显示扩展视角和路线。

数据包不进仓（`.gitignore` 里 `/02-data/` 一条）：两段视频合计约 105 MB，仓库只保留一份 2026-11-14 的旧批次兜底。**只合并代码不会让网站换素材**——必须把数据包装进部署环境的数据目录。

| 位置 | 内容 | 何时用 |
| --- | --- | --- |
| `$UAV_DATA_DIR/uav-inspection-backend/platedata/` | 正式录制批次 | 装了就用这一份 |
| `app/uav-inspection-backend/plateapp/data/` | 镜像内旧批次（p9_v2_final_20261114） | 运行期目录为空时的兜底 |

选择在进程启动时定死：运行期目录里只要存在 acceptance.json / timeline.json / coverage.json 任意一份，整目录就切到运行期；一份都没有则退回镜像内批次。所以装完必须重启。

## 部署（Docker）

容器用命名卷 `uav-data` 挂到 `/data`，即 `UAV_DATA_DIR=/data`。把数据包放进这个卷：

```bash
cd deploy
# 先自检，再拷进容器并重启，最后核对网站实际在用的批次
./scripts/install_platedata.sh /path/to/platedata
```

脚本依次做：结构检查 → `check_replay_data.py --strict-batch` → 备份旧包到 `platedata.bak-<时间戳>` → `docker cp` 进 `/data/uav-inspection-backend/platedata/` → `docker compose restart uav` → 用下面的接口断言来源与批次。

手工等价步骤（脚本不可用时）：

```bash
CID=$(sudo docker compose ps -q uav)
sudo docker exec "$CID" mkdir -p /data/uav-inspection-backend/platedata
sudo docker cp /path/to/platedata/. "$CID:/data/uav-inspection-backend/platedata/"
sudo docker compose restart uav
```

## 验证

```bash
curl -s http://127.0.0.1:18020/api/v1/plate/dataset
```

`/api/v1/plate/dataset` 报「网站实际在放哪一份」，而不只是「文件里写了什么」：

| 字段 | 该看到什么 | 说明 |
| --- | --- | --- |
| `source` | `runtime` | `packaged` 表示数据包没落到运行期目录，页面在放镜像里的旧批次 |
| `mission.run` | `mission_20261121_235643` | 与顶栏「任务编号」一致 |
| `path.steps` / `path.fallback` | `43` / `false` | `fallback: true` 表示 path.json 缺失、路线是现场拼的示意线 |
| `rviz.fallback` | `false` | `true` 表示第 2、3 格用的是主回放顶替 |
| `coverage_map` | `map_20261114_190944` | 覆盖热力图来自历史批次，见下节 |
| `missing` | `[]` | 非空即数据包缺文件 |

装错批次的典型现象：`source` 仍是 `packaged`、`mission.run` 是 `p9_v2_final_20261114`——数据包没拷进卷，或拷完没重启。

## 本批素材来源

正式批次 `mission_20261121_235643`（2026-11-21 23:56:43 起，FSM DONE，拍成率 1.000，7/7 辆）。同一批里有三处数据不是本轮独立产出的，页面与接口都按原样标注：

| 数据 | 来源 | 页面表现 |
| --- | --- | --- |
| 主回放视频、RViz 录屏、path.json、车辆结果与取证缩略图 | 本批（`mission_20261121_235643`） | — |
| 覆盖热力图与覆盖率分档 | `map_20261114_190944`（历史建图批次） | `coverage.source.map_dir` 原样返回，`/dataset` 的 `coverage_map` 可查 |
| 违停判定的车位几何 | 早前一轮单独测量，本批沿用同一套 | 「违停判定」标题的悬停提示写明取自哪一轮、本次只验拍得到读得对 |

车辆 7/7 的口径是「按车牌真值辅助优选拍摄腿后逐字比对，用于回放展示」，不是本轮从零独立识别；验收 JSON 的 `summary[1].note` 保留这句原文。

## 生成脚本

仓库根目录的 scripts/build_replay.py 生成事件时间轴，scripts/onsite/gen_path_json.py 生成路径。安装前运行以下检查，并确认两段视频的起始偏移量均来自同一批次的实测记录。

```bash
python scripts/check_replay_data.py --data <platedata-directory> --strict-batch
```

本批实测：15 项通过、0 失败、1 个 WARN。WARN 是「RViz 录屏批次与覆盖质量数据不同批」——录屏来自本批、覆盖热力图来自 11-14 的历史建图，属预期，不做修改。

注意 path.json 必须逐批重建：它的 `t` 是任务秒，和 timeline 的 `video.offset_s` 同一条时间轴。换批次只换视频不换 path.json，第四格会在开头几分钟一直停在「待起飞」。
