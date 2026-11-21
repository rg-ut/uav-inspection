# 无人机车牌巡检回放

平台首页的「无人机车牌巡检」进入 /plate。页面以全景视频为主时钟，根据各视频记录起点的偏移量同步机上画面与拍照位置。第四格根据任务日志中的视点和时间绘制路线，关键事件随任务时间更新。

新批次通过平台数据目录交付。在 UAV_DATA_DIR 下安装完整的 uav-inspection-backend/platedata/，包含 timeline.json、coverage.json、acceptance.json、cards/ 和 media/。media/ 中包含主视频、RViz 录屏、path.json 和页面引用的封面。安装后重启后端以加载新批次。未配置新批次时，页面读取仓库附带的旧批次，只有提供相应素材时才显示扩展视角和路线。

仓库根目录的 scripts/build_replay.py 生成事件时间轴，scripts/onsite/gen_path_json.py 生成路径。安装前运行以下检查，并确认两段视频的起始偏移量均来自同一批次的实测记录。

```bash
python scripts/check_replay_data.py --data <platedata-directory> --strict-batch
```
