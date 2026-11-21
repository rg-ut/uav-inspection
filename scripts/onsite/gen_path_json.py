"""从一次跑批的 stage2 日志与 inspect 清单重建 `platedata/media/path.json`。

## 为什么需要这个脚本

`path.json` 是第四格「路径与视点」的数据源：把日志里的 GOTO 序列复算成俯视坐标，
每条腿带一个**任务秒**时间戳 `t`，前端拿 `videoTime + video.offset_s` 去比，走到哪条腿就点亮到哪。

它必须**逐批重建**。2026-11-21 就是漏了这一步：其余三份 JSON 与全部素材都换成了 p11 那批，
path.json 还是 p12 的（`source` 里写着 `mission_20261116_120348`，首步 `t=401`），
结果回放走了 5 分钟、第四格仍停在「第 0 步」——因为新批第一个 GOTO 在 415 s，
401 之前一条都匹配不上。看上去就像「这一格没跟时间同步」。

## 两个口径（都跟旧数据核对过，误差 < 0.01 m）

1. **`front/*` 的平面位置 = 车坐标沿车头方向前推 `d` 米**，`d` 直接取 GOTO 行里的 `d=`。
   车坐标与 yaw 取 `inspect_r*.yaml`（mission_runner 生成的那份，与实际起飞位置一致）。
2. **`transit/*` 取车坐标本身**（实际在车上方），**`FLEET` 取场地中心 (0,0)**。
   高度一律不画——这一格是俯视图，画了反而看不出平面关系。

## 用法（在实机上跑，日志都在跑批目录里）

    python3 gen_path_json.py \\
        --run-dir ~/runs/mission_20261121_153741 \\
        --record-log ~/runs/p11_accept_20261121_153738/record.log \\
        --offset-s 8.3 \\
        --out /tmp/path.json

`--offset-s` 取 `timeline.json` 的 `video.offset_s`：它是「录屏起点比任务起跑晚多少秒」，
脚本用 record.log 里那行「录制开始」的 ROS 时间戳反推任务起跑时刻，`t` 才和视频同一条时间轴。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re

# [INFO] [1795247076.825823]: [VP] GOTO #1 FLEET overview/all d=12.77
GOTO_RE = re.compile(r"\[INFO\] \[(\d+\.\d+)\]: \[VP\] GOTO #(\d+) (\S+) (\S+) d=([\d.]+)")
# - {name: car_0, x: 8.2960, y: 2.4630, z: 0.0000, yaw: -2.905000, plate: '皖AB3618', faces: [front]}
CAR_RE = re.compile(
    r"name:\s*(\w+),\s*x:\s*(-?[\d.]+),\s*y:\s*(-?[\d.]+),\s*z:\s*-?[\d.]+,\s*"
    r"yaw:\s*(-?[\d.]+),\s*plate:\s*'([^']*)'"
)
REC_RE = re.compile(r"\[INFO\] \[(\d+\.\d+)\]: 录制开始")
# [INFO] [1795247190.572646]: [VP] car_0 触发补拍（1 条腿全部格式不合法）：#5 插 front/retry …
RETRY_RE = re.compile(r"\[VP\] (\S+) 触发补拍（([^）]*)）")

DEFAULT_AREA = {"x0": -13.7, "x1": 13.7, "y0": -9.2, "y1": 9.2}


def load_cars(run_dir: str) -> dict[str, dict]:
    """合并所有 inspect_r*.yaml。r2 只含补拍车，同一辆车的位置与 r1 相同。"""
    cars: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "inspect_r*.yaml"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = CAR_RE.search(line)
                if not m:
                    continue
                name, x, y, yaw, plate = m.groups()
                cars[name] = {
                    "name": name,
                    "x": round(float(x), 3),
                    "y": round(float(y), 3),
                    "yaw": round(float(yaw), 4),
                    "plate": plate,
                }
    return cars


def mission_start_epoch(record_log: str, offset_s: float) -> float:
    """任务起跑时刻 = 「录制开始」的 ROS 时间戳 − offset_s。与 timeline 同一条时间轴。"""
    with open(record_log, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = REC_RE.search(line)
            if m:
                return float(m.group(1)) - float(offset_s)
    raise SystemExit("record.log 里没有「录制开始」行，无法定位任务起跑时刻：%s" % record_log)


def stage_logs(run_dir: str) -> list[str]:
    """按轮次号排序，别用 glob 的字典序 —— r10 会排到 r2 前面。"""
    found = glob.glob(os.path.join(run_dir, "stage2_r*.log"))
    def order(p: str) -> int:
        m = re.search(r"stage2_r(\d+)\.log$", p)
        return int(m.group(1)) if m else 0
    return sorted(found, key=order)


def build_steps(run_dir: str, cars: dict[str, dict], start_epoch: float) -> list[dict]:
    steps: list[dict] = []
    # 补拍原因：`[VP] car_0 触发补拍（1 条腿全部格式不合法）` 打在决定补拍的那一刻，
    # 比对应那条 front/retry 的 GOTO 早几十秒，所以按车排队、遇到 front/retry 时取队首。
    why_q: dict[str, list[str]] = {}
    for path in stage_logs(run_dir):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                r = RETRY_RE.search(line)
                if r:
                    why_q.setdefault(r.group(1), []).append(r.group(2).strip())
                    continue
                m = GOTO_RE.search(line)
                if not m:
                    continue
                epoch, num, car, leg, d = m.groups()
                d = float(d)
                if car == "FLEET":
                    kind = "overview"
                    x, y, yaw = 0.0, 0.0, 0.0
                elif car not in cars:
                    # 车不在本轮清单里（理论上不会发生）：跳过并留痕，别静默画到原点
                    print("  [!] %s 不在 inspect 清单里，跳过 GOTO #%s %s" % (car, num, leg))
                    continue
                else:
                    c = cars[car]
                    kind = "capture" if leg.startswith("front") else "transit"
                    yaw = c["yaw"]
                    if kind == "capture":
                        # 车头方向前推 d 米
                        x = c["x"] + d * math.cos(yaw)
                        y = c["y"] + d * math.sin(yaw)
                    else:
                        x, y = c["x"], c["y"]
                step = {
                    "i": int(num),
                    "t": round(float(epoch) - start_epoch, 1),
                    "car": car,
                    "leg": leg,
                    "d": round(d, 2),
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "yaw": round(yaw, 4),
                    "kind": kind,
                }
                if leg == "front/retry" and why_q.get(car):
                    step["why"] = why_q[car].pop(0)
                steps.append(step)
    steps.sort(key=lambda s: s["t"])
    return steps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="~/runs/mission_<id>")
    ap.add_argument("--record-log", required=True, help="该批的 record.log（取「录制开始」时间戳）")
    ap.add_argument("--offset-s", type=float, required=True, help="timeline.video.offset_s")
    ap.add_argument("--out", required=True)
    ap.add_argument("--note", default=None)
    a = ap.parse_args()

    cars = load_cars(a.run_dir)
    if not cars:
        raise SystemExit("没解析出车辆：%s 下没有 inspect_r*.yaml" % a.run_dir)
    start = mission_start_epoch(a.record_log, a.offset_s)
    steps = build_steps(a.run_dir, cars, start)
    if not steps:
        raise SystemExit("没解析出 GOTO：%s 下没有 stage2_r*.log 或格式变了" % a.run_dir)

    doc = {
        "source": "amov007:%s  %s" % (
            a.run_dir,
            " / ".join(os.path.basename(p) for p in stage_logs(a.run_dir)) + " / inspect_r*.yaml",
        ),
        "note": a.note or (
            "front/* 视点按车轴方位前推 d 米复算平面位置；transit/* 平面取车坐标（实际在车上方）；"
            "FLEET 取场地中心。高度未画。t 为任务秒（与 timeline.video.offset_s 同一时间轴）。"
        ),
        "area": DEFAULT_AREA,
        "cars": [cars[k] for k in sorted(cars)],
        "steps": steps,
    }
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    kinds = {}
    for s in steps:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    n_why = sum(1 for s in steps if s.get("why"))
    print("写出 %s" % a.out)
    print("  %d 辆车 / %d 条腿（%s）/ t 范围 %.1f..%.1f s / 带补拍原因 %d 条"
          % (len(doc["cars"]), len(steps),
             "，".join("%s %d" % kv for kv in sorted(kinds.items())),
             steps[0]["t"], steps[-1]["t"], n_why))


if __name__ == "__main__":
    main()
