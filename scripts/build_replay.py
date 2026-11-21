# -*- coding: utf-8 -*-
"""把一次跑批的产物，重新生成车牌模块「过程回放」页要用的 `timeline.json`。

## 为什么要这个脚本

`plateapp/api/detect.py` 只**读** `data/timeline.json`，仓库里没有任何东西**写**它。
结果就是：换一批跑批数据，回放页做不出来，只能手工照抄上一批的 JSON 结构。
本脚本补上这一环，输入全是跑批自己留下的文件，不猜、不填假数。

## 输入（缺一不可）

| 参数 | 是什么 | 从哪来 |
|---|---|---|
| `--record-log` | 演示录制日志 `record.log` | `~/runs/<run_name>/record.log` |
| `--fsm-json` | 状态机结果 `fsm.json` | `~/runs/<mission_id>/fsm.json`（`record.log` 里「详情」那行的路径） |
| `--frames` `--fps` | 录屏帧数与帧率 | `record.log` 的 `完成：N 帧` 与 `录制 : ... @15fps`；不传则自动解析 |

## 各字段的真源（一个都不编）

| 字段 | 来源 |
|---|---|
| `mission.run` | `--run-name`，默认取 `record.log` 所在目录名 |
| `mission.id` / `started_at` | `[runner HH:MM:SS] 开始：日志 <路径>` 的时间 + 文件头 `启动 : YYYY-MM-DD` |
| `mission.scenario` | 文件头 `场景 :` 的 world 名 + `车位表 N 个` + `车表 M 辆` |
| `mission.barrier` | `[park2] 遮挡 : car_0 （完全遮挡 0.60×0.60）` |
| `kpi.*` | **整个 `fsm.json`**（final / note / c_cap / n_cars / n_captured / missing / inspect_round / rounds 一一对应） |
| `budget.elapsed_s` | `收尾：降落 + 停 stage1（elapsed Ns）` |
| `budget.budget_s` | `[park2] 闭环 : ... 总预算 Ns` |
| `budget.wall_clock_s` | `== FSM 结果 ==` 段的 `墙钟 : Ns` |
| `video.frames` / `fps` / `size` | 文件头 `录制 : /watch/image_raw 1280x720 @15fps` + 末尾 `完成：N 帧` |
| `video.seconds` | `frames / fps`（与 ffprobe 实测一致，无需 ffmpeg） |
| `video.offset_s` | `（结束时刻 − 任务开始时刻）− seconds`：录屏在收尾时停，所以视频起点必然晚这么多 |
| `phases.*` | 由 `[runner]` 里程碑换算成「相对开始多少秒」 |
| `events` | 全部 `[FSM]` 行 + `[runner]` 行，**剔除 SURVEY 参数铺设那 6 行**（聚类半径/场景/车位清单/范围/起 stage1/日志），它们不是过程事件 |

## 不生成的东西（要另外做）

- `cover_*.jpg` / `poster.jpg` / `replay.mp4`：需要 ffmpeg 从 `watch_raw.mp4` 抽帧与转码，本机没有 ffmpeg，
  故本脚本只写 JSON。视频路径按 `--media-prefix` 拼，素材要另外放进 `data/media/`。
- `coverage.json`：见 `docs/plate-replay-data.md` 的 TODO，口径未记录全，不在此猜。

## 用法

    # 1) 先看它算出来的，和盘上现有的比一比
    python scripts/build_replay.py timeline \
        --record-log ~/runs/p9_v2_final_20261114/record.log \
        --fsm-json  ~/runs/mission_20261114_190947/fsm.json \
        --out /tmp/timeline.json --diff 02-data/uav-inspection-backend/platedata/timeline.json

    # 2) 确认无误再覆盖
    python scripts/build_replay.py timeline --record-log ... --fsm-json ... \
        --out 02-data/uav-inspection-backend/platedata/timeline.json

退出码：0 = 写出成功（带 --diff 时还要求无差异）；1 = 解析失败或有差异；2 = 输入缺失。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

TS = re.compile(r"^\[runner (\d{2}):(\d{2}):(\d{2})\] (.*)$")
FSM = re.compile(r"^\[FSM\]\s?(.*)$")
START = re.compile(r"开始：日志\s+(\S+)")

# SURVEY 的参数铺设行，不是过程事件
SKIP_RUNNER_PREFIX = ("SURVEY 聚类半径", "SURVEY 场景", "SURVEY 车位清单",
                      "SURVEY 范围", "SURVEY 起 stage1")


def read_text(path):
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with io.open(path, encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
    raise SystemExit("读不了 %s（试过 utf-8 / gbk）" % path)


def hhmmss_to_s(h, m, s, base):
    """把 HH:MM:SS 转成「相对 base（base 也是秒数）」的秒；跨零点自动 +86400。"""
    t = int(h) * 3600 + int(m) * 60 + int(s)
    d = t - base
    return d + 86400 if d < 0 else d


def parse_record(path):
    text = read_text(path)
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    out = {"header": {}, "park": {}, "events": [], "raw": lines}

    for ln in lines:
        s = ln.strip()
        if s.startswith("启动") and ":" in s and "启动" not in out["header"]:
            out["header"]["启动"] = s.split(":", 1)[1].strip()
        elif s.startswith("场景") and ":" in s:
            out["header"]["场景"] = s.split(":", 1)[1].strip()
        elif s.startswith("车表") and ":" in s:
            out["header"]["车表"] = s.split(":", 1)[1].strip()
        elif s.startswith("机位") and ":" in s:
            out["header"]["机位"] = s.split(":", 1)[1].strip()
        elif s.startswith("录制") and ":" in s:
            out["header"]["录制"] = s.split(":", 1)[1].strip()
        elif s.startswith("结束") and ":" in s:
            out["header"]["结束"] = s.split(":", 1)[1].strip()
        elif s.startswith("完成：") and "帧" in s:
            m = re.search(r"完成：\s*(\d+)\s*帧", s)
            if m:
                out["header"]["总帧数"] = int(m.group(1))
        elif s.startswith("[park2]"):
            body = s[len("[park2]"):].strip()
            if ":" in body:
                k, v = body.split(":", 1)
                out["park"][k.strip()] = v.strip()

    # 事件：先定位开始时刻
    start_base = None
    for ln in lines:
        m = TS.match(ln)
        if m and start_base is None:
            start_base = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    if start_base is None:
        raise SystemExit("record.log 里找不到 [runner HH:MM:SS] 行，无法定位任务开始")

    for ln in lines:
        m = TS.match(ln)
        if m:
            t = hhmmss_to_s(m.group(1), m.group(2), m.group(3), start_base)
            body = m.group(4)
            stripped = body.strip()
            # 日志行前面带缩进，必须 strip 后再判
            if stripped.startswith(SKIP_RUNNER_PREFIX) or stripped.startswith("日志"):
                continue
            out["events"].append({"t": t, "kind": "runner", "text": body})
            continue
        m = FSM.match(ln)
        if m:
            # FSM 行没有独立时间戳，取「上一条 runner 的时间」——同一秒内打印
            t = out["events"][-1]["t"] if out["events"] else 0
            out["events"].append({"t": t, "kind": "fsm", "text": m.group(1)})

    out["start_base"] = start_base
    out["start_s"] = start_base
    return out


def find(path_or_none):
    return path_or_none if path_or_none and os.path.isfile(path_or_none) else None


def build(cfg):
    rec = parse_record(cfg.record_log)
    if not cfg.fsm_json or not os.path.isfile(cfg.fsm_json):
        raise SystemExit("缺 --fsm-json：kpi 的唯一真源是 fsm.json，不能从日志反推")
    with io.open(cfg.fsm_json, encoding="utf-8") as fh:
        fsm = json.load(fh)

    hd, park, ev = rec["header"], rec["park"], rec["events"]

    # ---- mission ----
    started = hd.get("启动", "")
    date = started.split()[0] if started else ""
    m = START.search(" ".join(hd.get("场景", ""), ) + " " + "\n".join(
        ln for ln in rec["raw"] if ln.startswith("[runner") and "开始：日志" in ln))
    mission_dir = None
    for ln in rec["raw"]:
        mm = START.search(ln)
        if mm:
            mission_dir = mm.group(1)
            break
    hh = rec["start_base"] // 3600
    mm_ = (rec["start_base"] % 3600) // 60
    ss = rec["start_base"] % 60
    started_at = "%s %02d:%02d:%02d" % (date, hh, mm_, ss) if date else ""

    world = ""
    mw = re.match(r"([\w.\-]+\.world)", hd.get("场景", ""))
    if mw:
        world = mw.group(1)
    n_slots = ""
    ms = re.search(r"（(\d+)\s*个）", park.get("车位表", ""))
    if ms:
        n_slots = ms.group(1) + " 车位"
    n_cars = ""
    mc = re.search(r"（(\d+)\s*辆）", hd.get("车表", ""))
    if mc:
        n_cars = mc.group(1) + " 辆车"
    scenario = " · ".join(x for x in (world, n_slots + " / " + n_cars if n_slots and n_cars else (n_slots or n_cars)) if x)

    barrier = ""
    mb = re.match(r"(\S+)\s*（完全遮挡\s*([\d.×x]+)）", park.get("遮挡", ""))
    if mb:
        barrier = "%s（%s 挡板）" % (mb.group(1), mb.group(2))

    # ---- kpi：直接取 fsm.json，一一对应 ----
    ctx = fsm.get("ctx", {})
    kpi = {
        "final": fsm.get("final"),
        "note": fsm.get("note"),
        "c_cap": ctx.get("c_cap"),
        "n_cars": ctx.get("n_cars"),
        "n_captured": ctx.get("n_captured"),
        "missing": ctx.get("missing", []),
        "inspect_round": ctx.get("inspect_round"),
        "rounds": fsm.get("rounds", []),
    }

    # ---- budget ----
    wall = None
    mw2 = re.search(r"墙钟\s*:\s*(\d+)\s*s", "\n".join(rec["raw"]))
    if mw2:
        wall = int(mw2.group(1))
    elapsed = None
    me = re.search(r"elapsed\s+(\d+)s", "\n".join(rec["raw"]))
    if me:
        elapsed = int(me.group(1))
    budget_s = None
    mbg = re.search(r"总预算\s*(\d+)s", park.get("闭环", ""))
    if mbg:
        budget_s = int(mbg.group(1))

    # ---- video ----
    size = fps = None
    mv = re.search(r"(\d+x\d+)\s*@\s*([\d.]+)\s*fps", hd.get("录制", ""))
    if mv:
        size, fps = mv.group(1), float(mv.group(2))
    frames = cfg.frames or hd.get("总帧数")
    seconds = round(frames / fps, 1) if (frames and fps) else None
    offset = cfg.offset_s
    offset_src = "参数指定" if offset is not None else None
    if offset is None and started:
        # 优先：record.log 的「[INFO] [<unix>]: 录制开始」——这是录屏真正的起点。
        # 不能用「脚本结束时刻 − 视频秒数」：脚本收尾（停 stage1、建车位图、
        # 写 DONE.rc）比录屏结束晚约 24 s，会把 offset 系统性多算 24 s。
        # 实测代价：p9 算成 29.5（真实 6.0）、p10 算成 33.0（真实 8.7），
        # 后果是所有「跳段首」整体晚 24 s，1 秒长的 wrap 段直接跳到别的段里。
        try:
            from datetime import datetime
            mrec = re.search(r"\[INFO\] \[(\d+\.\d+)\]: 录制开始",
                             "\n".join(rec["raw"]))
            if mrec:
                d0 = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                d_rec = datetime.fromtimestamp(float(mrec.group(1)))
                offset = round((d_rec - d0).total_seconds(), 1)
                offset_src = "录制开始时间戳"
        except Exception:                                     # noqa: BLE001
            offset = None

    if offset is None and seconds and hd.get("结束") and started:
        # 兜底：拿不到时间戳时才用脚本结束时刻，已知偏大约 24 s，note 会标出来
        try:
            from datetime import datetime
            fmt = "%Y-%m-%d %H:%M:%S"
            d0 = datetime.strptime(started_at, fmt)
            d1 = datetime.strptime(hd["结束"], fmt)
            offset = round((d1 - d0).total_seconds() - seconds, 1)
            offset_src = "脚本结束时刻（偏大，不可靠）"
        except Exception:                                     # noqa: BLE001
            offset = None

    # ---- phases：从里程碑切 ----
    def t_of(pred):
        for e in ev:
            if pred(e):
                return e["t"]
        return None

    t_survey_end = t_of(lambda e: e["kind"] == "runner" and e["text"].startswith("SURVEY 完成"))
    n_found = None
    mo = re.search(r"发现\s*(\d+)\s*条", next(
        (e["text"] for e in ev if e["text"].startswith("SURVEY 完成")), ""))
    if mo:
        n_found = int(mo.group(1))

    round_t0, round_t1 = {}, {}
    for i, e in enumerate(ev):
        m1 = re.match(r"INSPECT 第 (\d+) 轮：", e["text"])
        if m1:
            n = int(m1.group(1))
            t = e["t"]
            for j in range(i - 1, -1, -1):
                if ev[j]["text"].startswith("遮挡法"):
                    t = ev[j]["t"]
                    break
            round_t0[n] = t
        m2 = re.match(r"INSPECT 第 (\d+) 轮结束 rc=(\d+)", e["text"])
        if m2:
            round_t1[int(m2.group(1))] = e["t"]

    cap_of, rc_of = {}, {}
    for e in ev:
        m3 = re.match(r"INSPECT 第 (\d+) 轮产物：.*拍成 (\d+) 辆", e["text"])
        if m3:
            cap_of[int(m3.group(1))] = int(m3.group(2))
        m4 = re.match(r"INSPECT 第 (\d+) 轮结束 rc=(\d+)", e["text"])
        if m4:
            rc_of[int(m4.group(1))] = int(m4.group(2))

    t_wrap = t_of(lambda e: e["kind"] == "runner" and e["text"].startswith("收尾："))

    phases = []
    if t_survey_end is not None:
        phases.append({
            "key": "survey", "label": "SURVEY 巡航发现", "kind": "survey",
            "t0": 0, "t1": t_survey_end, "seconds": t_survey_end,
            "detail": "发现 %s 条（%d s）" % (n_found if n_found is not None else "?", t_survey_end),
            "cover": cfg.media("cover_survey.jpg"),
        })
    for n in sorted(round_t0):
        if n not in round_t1:
            continue
        t0, t1 = round_t0[n], round_t1[n]
        cap = cap_of.get(n, 0)
        rc = rc_of.get(n, 0)
        detail = "拍成 %d 辆" % cap + ("" if rc == 0 else "（rc=%d）" % rc)
        phases.append({
            "key": "inspect_r%d" % n, "label": "INSPECT 第 %d 轮" % n,
            "kind": "inspect", "t0": t0, "t1": t1, "seconds": t1 - t0,
            "detail": detail, "cover": cfg.media("cover_inspect_r%d.jpg" % n),
        })
    if t_wrap is not None and wall:
        phases.append({
            "key": "wrap", "label": "收尾降落", "kind": "wrap",
            "t0": t_wrap, "t1": wall, "seconds": wall - t_wrap,
            "detail": "停 stage1、清场", "cover": cfg.media("cover_wrap.jpg"),
        })

    doc = {
        "mission": {
            "run": cfg.run_name or os.path.basename(os.path.dirname(os.path.abspath(cfg.record_log))),
            "id": os.path.basename(mission_dir) if mission_dir else "",
            "started_at": started_at,
            "scenario": scenario,
            "platform": cfg.platform,
            "barrier": barrier,
        },
        "kpi": kpi,
        "budget": {"wall_clock_s": wall, "elapsed_s": elapsed, "budget_s": budget_s},
        "video": {
            "src": cfg.media("replay.mp4"),
            "poster": cfg.media("poster.jpg"),
            "seconds": seconds,
            "frames": frames,
            "fps": fps,
            "size": size,
            "offset_s": offset,
            "note": ("录屏晚于任务启动 %s s（来源：%s），视频时间 = 墙钟 − %s"
                     % (offset, offset_src, offset)
                     if offset is not None
                     else "offset 未能推算：缺「录制开始」时间戳与结束时刻"),
        },
        "phases": phases,
        "events": ev,
    }
    return doc


def diff_docs(new, old, path="$"):
    """逐字段比对，输出差异（只报前 40 条，避免刷屏）。"""
    out = []

    def walk(a, b, p):
        if len(out) > 40:
            return
        if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            out.append("%s 类型不同：%s vs %s" % (p, type(a).__name__, type(b).__name__))
            return
        if isinstance(a, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a:
                    out.append("%s.%s 只在盘上文件里有（脚本没产出）" % (p, k))
                elif k not in b:
                    out.append("%s.%s 只在脚本输出里有（盘上文件没有）" % (p, k))
                else:
                    walk(a[k], b[k], "%s.%s" % (p, k))
        elif isinstance(a, list):
            if len(a) != len(b):
                out.append("%s 长度不同：%d vs %d" % (p, len(a), len(b)))
            for i in range(min(len(a), len(b))):
                walk(a[i], b[i], "%s[%d]" % (p, i))
        else:
            if a != b:
                out.append("%s 不同：%r vs %r" % (p, a, b))

    walk(new, old, path)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="重新生成车牌回放页的 timeline.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("timeline", help="从 record.log + fsm.json 生成 timeline.json")
    t.add_argument("--record-log", required=True)
    t.add_argument("--fsm-json", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--diff", default=None, help="与这个已有文件逐字段比对，有差异则退出码 1")
    t.add_argument("--run-name", default=None, help="默认取 record.log 所在目录名")
    t.add_argument("--frames", type=int, default=None, help="默认从 record.log 解析")
    t.add_argument("--offset-s", type=float, default=None, help="录屏起始偏移（秒），默认推算")
    t.add_argument("--platform", default="P450 / Gazebo 仿真")
    t.add_argument("--media-prefix", default="/api/v1/plate/media")
    a = ap.parse_args(argv)

    a.media = lambda fn: "%s/%s" % (a.media_prefix.rstrip("/"), fn)
    doc = build(a)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("已写出 %s" % a.out)
    print("  事件 %d 条、阶段 %d 段、rounds %d 条"
          % (len(doc["events"]), len(doc["phases"]), len(doc["kpi"]["rounds"])))
    print("  mission.id=%s  wall=%s  elapsed=%s  frames=%s/%sfps  video=%.1fs  offset=%s"
          % (doc["mission"]["id"], doc["budget"]["wall_clock_s"],
             doc["budget"]["elapsed_s"], doc["video"]["frames"], doc["video"]["fps"],
             doc["video"]["seconds"] or 0, doc["video"]["offset_s"]))

    if a.diff:
        with io.open(a.diff, encoding="utf-8") as fh:
            old = json.load(fh)
        ds = diff_docs(doc, old)
        if ds:
            print("\n与 %s 有 %d 处差异：" % (a.diff, len(ds)))
            for d in ds:
                print("  - %s" % d)
            return 1
        print("\n与 %s 逐字段一致" % a.diff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
