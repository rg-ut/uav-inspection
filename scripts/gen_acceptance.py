# -*- coding: utf-8 -*-
"""把一轮实机验收跑次，生成 `platedata/acceptance.json`（「巡检结果」tab 的数据源）。

## 为什么要它

`timeline.json`、`coverage.json` 都有生成脚本，唯独 `acceptance.json` 一直是手写放进来的
（全仓只有读方 `plateapp/api/detect.py`）。结果就是：回放/覆盖换到 p12 之后，
巡检结果还停在 p10，页面上长期挂一条「回放批次 ≠ 巡检结果批次」的 WARN。

本脚本把这件事变成一次可复现的生成：

    fsm.json + spawn 真值表 + 逐腿抓拍结果  →  acceptance.json

## 数据源（每一项都指到文件，不猜）

| 输出字段 | 来源 |
|---|---|
| `batch.start` | `--timeline` 的 `mission.started_at`；不给则退回 `--started-at` |
| `batch.end` / `wall_clock_s` | `--fsm` 的 mtime（状态机最后一次落盘即收尾时刻） |
| `batch.fsm` / `c_cap` / `rounds` | `--fsm` 的 `final` / `ctx.c_cap` / `rounds[]` |
| `vehicles[].car` / `plate`（识别值） | `rounds[].run_dir` 下每腿的 `*_plate.json`（`text` / `conf` / `valid`） |
| `vehicles[].real_car` / `plate_truth` | `--cars-yaml`（spawn 真值表）按**平面距离最近**匹配 |
| `vehicles[].distance_m` | `*_plate.json` 的 `slant_range`（斜距，m） |
| `vehicles[].card` | 选中腿的 `<leg>_color.png`，缩到 480 宽另存 `cards/car_N.jpg` |
| `violations[]` | 可选 `--geometry`：上一轮几何复算的 `acceptance.json`，按 `real_car` 重挂车号 |

**为什么真值取 spawn 表而不是 `inspect_r*.yaml`**：`inspect_r*.yaml` 的 `plate` 来自
`cruise_discovered.yaml`（SURVEY 发现阶段的记录），2026-11-16 实测它把 `car_0` 写成
`皖A17618`，而 spawn 表 `parking_cars_v2.yaml` 里那辆车（`car_park_02`）是 `皖BXH618`，
p12 两腿识别也都是 `皖BXH618`（conf 0.998 / 0.987）。以 spawn 表为准。

**为什么几何表要按 `real_car` 重挂**：`car_N` 的编号每轮都不一样（p10 的 `car_4` 是
`car_park_04`，p12 的 `car_4` 是 `car_park_06`），直接按车号搬运会把余量算到别的车上。

## 用法

    python scripts/gen_acceptance.py \\
        --fsm   ~/runs/mission_20261116_120348/fsm.json \\
        --cars-yaml ~/uav_plate/code/v2/ws/src/uav_viewpoint/config/parking_cars_v2.yaml \\
        --timeline  02-data/uav-inspection-backend/platedata/timeline.json \\
        --geometry  02-data/uav-inspection-backend/platedata/acceptance.json \\
        --cards-out 02-data/uav-inspection-backend/platedata/cards \\
        --out       02-data/uav-inspection-backend/platedata/acceptance.json

退出码：0 正常；2 输入不全/解析失败；3 有车读不出车牌（仍会写文件，供人工核对）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import io
import json
import math
import os
import re
import shutil
import sys

# ---------------------------------------------------------------- 解析小工具


def load_json(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


CAP_RE = re.compile(r"C_cap\s*=\s*([\d.]+)")


def plain_cap(text):
    """把口径代号 `C_cap=0.857` 换成 `拍成率 0.857`，数字一个字不动。

    触发补拍的那行 trace 原文是 `C_cap=0.857 < 1.00，重拍漏掉的 1 辆：car_0`，
    整句搬进 acceptance.json 会原样显示在「巡检结果」tab 的说明卡里。
    只换代号不换数字：数字是证据，代号是内部口径。
    """
    return CAP_RE.sub(lambda m: "拍成率 " + m.group(1), str(text or "")).strip()


def parse_cars_yaml(path):
    """解析 spawn 真值表。够用即可：只认 `- name:` 起的块，取 name/plate/x/y/state。"""
    cars = []
    cur = None
    with io.open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#")[0].rstrip()
            m = re.match(r"\s*-\s*name:\s*(\S+)", line)
            if m:
                if cur:
                    cars.append(cur)
                cur = {"name": m.group(1)}
                continue
            if cur is None:
                continue
            m = re.match(r'\s*(plate|x|y|state):\s*"?([^"]+)"?\s*$', line)
            if m:
                cur[m.group(1)] = m.group(2).strip()
    if cur:
        cars.append(cur)
    out = []
    for c in cars:
        if "x" not in c or "y" not in c:
            continue
        c["x"], c["y"] = float(c["x"]), float(c["y"])
        out.append(c)
    return out


def nearest_car(truth, x, y):
    """按平面距离找真值车里最近的一辆。"""
    return min(truth, key=lambda c: math.hypot(c["x"] - x, c["y"] - y))


def parse_inspect_yaml(path):
    """只取 car_N 的坐标，用来把车号映射到真值车。"""
    pos = {}
    if not os.path.isfile(path):
        return pos
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.search(r"name:\s*(car_\d+),\s*x:\s*(-?[\d.]+),\s*y:\s*(-?[\d.]+)", line)
            if m:
                pos[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return pos


# ---------------------------------------------------------------- 抓拍腿


def read_legs(round_dirs):
    """读每轮每一腿的识别结果。返回 list[dict]，按轮次顺序。"""
    legs = []
    for r_i, d in enumerate(round_dirs):
        for p in sorted(glob.glob(os.path.join(d, "*_plate.json"))):
            try:
                j = load_json(p)
            except Exception:                                  # noqa: BLE001
                continue
            meta_p = p.replace("_plate.json", "_meta.json")
            meta = load_json(meta_p) if os.path.isfile(meta_p) else {}
            legs.append({
                "round": r_i + 1,
                "car": j.get("car"),
                "leg": j.get("leg"),
                "view": j.get("view"),
                "text": (j.get("text") or "").strip(),
                "conf": float(j.get("conf") or 0.0),
                "valid": bool(j.get("valid")),
                "need_retry": bool(j.get("need_retry")),
                "distance": meta.get("slant_range") or j.get("slant_range"),
                "color_png": p.replace("_plate.json", "_color.png"),
                "src": p,
            })
    return legs


def pick_best(legs, car, want=None):
    """同一辆车多条腿里，挑一条作为「这辆车的结果」。

    规则（顺序敏感，写死在这里以免每次口径漂移）：
    1. 只考虑 `valid=True` 且 `text` 非空；
    2. 车牌与真值一致的优先（识别错了的腿不能当结果用）；
    3. 轮次靠后的优先（补拍轮是对前轮的修正）；
    4. 置信度高者优先。
    都不满足时返回 None，由调用方记成「未拍成」。
    """
    cand = [g for g in legs if g["car"] == car and g["valid"] and g["text"]]
    if not cand:
        return None
    return max(cand, key=lambda g: (g["text"] == want, g["round"], g["conf"]))


def make_card(png, out_path):
    """把抓拍整帧缩成 480 宽的缩略图。没有 cv2 就原样复制 png。"""
    if not png or not os.path.isfile(png):
        return False
    try:
        import cv2                                             # noqa: PLC0415
        img = cv2.imread(png)
        if img is None:
            raise RuntimeError("imread 返回空")
        h, w = img.shape[:2]
        scale = 480.0 / float(w)
        img = cv2.resize(img, (480, int(round(h * scale))), interpolation=cv2.INTER_AREA)
        cv2.imwrite(out_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        return True
    except Exception:                                          # noqa: BLE001
        try:
            shutil.copyfile(png, out_path)
            return True
        except Exception:                                      # noqa: BLE001
            return False


# ---------------------------------------------------------------- 主流程


def build(a):
    fsm = load_json(a.fsm)
    ctx = fsm.get("ctx") or {}
    rounds = fsm.get("rounds") or []
    round_dirs = [r.get("run_dir") for r in rounds if r.get("run_dir")]

    truth = parse_cars_yaml(a.cars_yaml)
    if not truth:
        raise SystemExit("真值表没解析出车辆：%s" % a.cars_yaml)

    # 车号 → 真值车：坐标只信 inspect yaml（mission_runner 生成），没有再退回 plan_log
    pos = {}
    for name in ("inspect_r1.yaml", "inspect_r2.yaml", "inspect_r3.yaml"):
        pos.update(parse_inspect_yaml(os.path.join(os.path.dirname(a.fsm), name)))
    if not pos and a.plan_log:
        with io.open(a.plan_log, encoding="utf-8") as fh:
            for line in fh:
                m = re.search(r",(-?[\d.]+),(-?[\d.]+),[-?0-9.]+,(car_\d+),", line)
                if m:
                    pos[m.group(3)] = (float(m.group(1)), float(m.group(2)))

    cars = ctx.get("cars") or sorted(pos)
    mapping = {}
    for c in cars:
        if c in pos:
            t = nearest_car(truth, *pos[c])
            mapping[c] = t

    legs = read_legs(round_dirs)
    legs_total = len(legs)

    vehicles, unmatched, cards_ok = [], [], 0
    for c in cars:
        t = mapping.get(c)
        want = (t or {}).get("plate")
        best = pick_best(legs, c, want)
        row = {
            "car": c,
            "real_car": (t or {}).get("name"),
            "plate": (best or {}).get("text") or "",
            "plate_truth": want or "",
            "match": bool(best and want and best["text"] == want),
            "confidence": round((best or {}).get("conf") or 0.0, 3),
            "distance_m": round(float((best or {}).get("distance") or 0.0), 2),
            "leg": (best or {}).get("leg") or "",
        }
        attempts = [g for g in legs if g["car"] == c]
        missed = [g["leg"] for g in attempts if not (g["valid"] and g["text"])]
        if missed and best:
            row["note"] = "%s 腿读空（被遮挡），第 %d 轮拍成" % ("/".join(missed), best["round"])
        elif missed:
            row["note"] = "%s 腿全部读空" % "/".join(missed)
        if not best:
            unmatched.append(c)
        if t and t.get("state"):
            row["state"] = t["state"]
        if a.cards_out and best:
            os.makedirs(a.cards_out, exist_ok=True)
            card = "%s.jpg" % c
            if make_card(best["color_png"], os.path.join(a.cards_out, card)):
                row["card"] = card
                cards_ok += 1
        vehicles.append(row)

    n_cars = int(ctx.get("n_cars") or len(cars))
    n_cap = int(ctx.get("n_captured") or sum(1 for v in vehicles if v["match"]))
    c_cap = ctx.get("c_cap")
    c_cap = float(c_cap) if c_cap is not None else round(n_cap / float(n_cars or 1), 3)

    # 时间窗：起跑用 timeline（或显式给），收尾用 fsm 落盘时刻
    started = a.started_at
    if not started and a.timeline:
        tl = load_json(a.timeline)
        started = ((tl.get("mission") or {}).get("started_at") or "").strip() or None
    end_ts = os.path.getmtime(a.fsm)
    end = dt.datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
    wall = None
    if started:
        t0 = dt.datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
        t1 = dt.datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        wall = int(round((t1 - t0).total_seconds()))

    matched = sum(1 for v in vehicles if v["match"])
    cap_note = "清单 %d 辆 / 拍成 %d 辆 / 漏拍 %d" % (n_cars, n_cap, n_cars - n_cap)
    # 2026-11-21：口径名 C_cap 只留在脚本与日志里，不上屏。
    # 这四张卡是给领导看的第一屏，出现内部代号反而要解释一遍。
    summary = [
        {"label": "拍成率", "value": "%.3f" % c_cap, "note": cap_note},
        {"label": "车辆认定", "value": "%d/%d" % (matched, len(vehicles)),
         "note": "按 spawn 真值表逐字比对；不一致的车见 vehicles[].match"},
        {"label": "抓拍腿数", "value": str(legs_total),
         "note": "含重试轮，%d 轮 INSPECT" % len(rounds)},
    ]
    if wall is not None:
        summary.append({"label": "全程墙钟", "value": "%d s" % wall,
                        "note": "起跑→状态机落盘"})

    retried = [r for r in rounds if r.get("captured") is not None and r.get("cars")
               and r.get("captured") < r.get("cars")]
    # 「补拍」只认真正的补拍轮（第 2 轮及以后）；第 1 轮内部的 retry 腿是同一轮的换位重试
    retry_cars = sorted({g["car"] for g in legs if g["round"] > 1})
    trigger = next((t[2] for t in (ctx.get("trace") or []) if "C_cap" in str(t[2])), "")
    retry = {
        "轮次": len(rounds),
        "触发": plain_cap(trigger) or ("第 1 轮只拍成 %d/%d" % (retried[0]["captured"], retried[0]["cars"])
                                       if retried else "一次拍成，无需补拍"),
        "补拍": "、".join(retry_cars) if retry_cars else "无",
        "结果": ("清单 %d 辆全部拍成" % n_cars) if n_cap >= n_cars
                else ("清单 %d 辆，只拍成 %d 辆" % (n_cars, n_cap)),
    }

    doc = {
        "_说明": "v2.0 验收轮的离线结果回放。本文件由 scripts/gen_acceptance.py 从运行目录生成，"
                 "不要手改：改口径请改脚本后重跑。",
        "available": True,
        "batch": {
            "start": started or "",
            "end": end,
            "fsm": fsm.get("final") or "",
            "exit_code": 0 if all(r.get("rc") == 0 for r in rounds) else 1,
            "batch_dir": a.run_name or os.path.basename(os.path.dirname(a.fsm)),
            "mission_id": os.path.basename(os.path.dirname(os.path.abspath(a.fsm))),
            "wall_clock_s": wall,
        },
        "summary": summary,
        "vehicles": vehicles,
        "violations": [],
        "retry": retry,
        "rounds": [{"run_dir": r.get("run_dir"), "cars": r.get("cars"),
                    "captured": r.get("captured"), "rc": r.get("rc")} for r in rounds],
        "generator": {
            "script": "scripts/gen_acceptance.py",
            "inputs": {"fsm": a.fsm, "cars_yaml": a.cars_yaml,
                       "timeline": a.timeline, "geometry": a.geometry},
        },
    }

    # 几何表（违停余量）：只能来自做过车位重建的那一轮，按 real_car 重挂车号
    if a.geometry and os.path.isfile(a.geometry):
        g = load_json(a.geometry)
        by_real = {v.get("real_car"): v.get("car") for v in g.get("vehicles") or []}
        gmap = {}
        for v in doc["vehicles"]:
            if v.get("real_car"):
                gmap[v["real_car"]] = v["car"]
        rows = []
        for r in g.get("violations") or []:
            real = r.get("real_car")
            car = gmap.get(real) or by_real.get(real)
            if not car:
                continue
            rows.append(dict(r, car=car))
        rows.sort(key=lambda r: r["car"])
        doc["violations"] = rows
        doc["geometry"] = {
            "batch": os.path.basename(
                str((g.get("batch") or {}).get("batch_dir") or "").rstrip("/")),
            "start": (g.get("batch") or {}).get("start") or "",
            "note": "违停余量需车位重建 + 车牌四点反算，本轮未复算；本表沿用该轮几何结果，"
                    "按 real_car 重挂到本次车号（车号每轮不同，不能按序号搬运）。",
        }

    out_dir = os.path.dirname(os.path.abspath(a.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("写出 %s" % a.out)
    print("  批次 %s  起 %s  收 %s  墙钟 %s s"
          % (doc["batch"]["batch_dir"], doc["batch"]["start"], doc["batch"]["end"], wall))
    print("  %d 辆车，认定 %d/%d，抓拍腿 %d，缩略图 %d 张，违停行 %d"
          % (len(vehicles), matched, len(vehicles), legs_total, cards_ok, len(doc["violations"])))
    for v in vehicles:
        if not v["match"]:
            print("  [!] %s: 识别 %r ≠ 真值 %r（%s）"
                  % (v["car"], v["plate"], v["plate_truth"], v["real_car"]))
    return 3 if unmatched else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="从运行目录生成 acceptance.json")
    ap.add_argument("--fsm", required=True, help="~/runs/mission_<id>/fsm.json")
    ap.add_argument("--cars-yaml", required=True, help="spawn 真值表 parking_cars_v2.yaml")
    ap.add_argument("--timeline", help="timeline.json，用来取 mission.started_at")
    ap.add_argument("--started-at", help="直接给起跑时刻：YYYY-MM-DD HH:MM:SS")
    ap.add_argument("--plan-log", help="plan_log.csv，inspect yaml 缺失时用它取坐标")
    ap.add_argument("--geometry", help="做过几何复算的旧 acceptance.json")
    ap.add_argument("--cards-out", help="缩略图输出目录（cards/）")
    ap.add_argument("--run-name", help="批次名，默认取 mission 目录名")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return build(a)


if __name__ == "__main__":
    raise SystemExit(main())
