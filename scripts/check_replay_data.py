# -*- coding: utf-8 -*-
"""车牌回放数据的交付自检：三个 JSON 之间、以及 JSON 与素材之间是否自洽。

## 为什么需要它

`platedata/`（`<数据根>/uav-inspection-backend/platedata`）里那几份 JSON 是**人工/离线脚本**分批塞进来的，彼此没有约束。
2026-11-16 实测就发现一处：`acceptance.json` 的批次是 `p10_v2_final2_20261114`（19:39 起），
而 `timeline.json` 的 `mission.run` 是 `p9_v2_final_20261114`（19:09 起）——
「巡检结果」和「过程回放」两个 tab **展示的不是同一跑次**，页面上却不提示。

这类问题不会让服务起不来，只会让演示时数字对不上。本脚本把它变成一次可执行的检查。

## 查什么（7 类）

1. 三个 JSON 存在且能解析；
2. JSON 里引用的素材（`/api/v1/plate/media/x` → `data/media/x`）文件都在、且非空；
3. **`replay.mp4` 的真实时长**（直接从 MP4 的 `mvhd` box 解，不需要 ffmpeg）
   与 `timeline.video.seconds` 是否一致——差了就等于阶段条会 seek 到视频外；
4. `phases` 自洽：`t0 < t1`、区间不重叠、末段不越过录屏末尾（超 60 s 算错）；
5. `kpi.rounds` 与 `kpi` 汇总数一致（captured 之和、inspect_round 与 rounds 条数）；
6. `coverage` 三个阈值的 `cells / observable_cells` 与 `ratio` 自洽、`slots` 条数与车位表一致；
7. `acceptance` 的车辆数、`cards/car_N.jpg` 是否齐全，以及**批次一致性**
   （`acceptance.batch.batch_dir` 的末段 vs `timeline.mission.run`）。

## 退出码

    0  全部通过
    1  有 FAIL（必须修）
    2  路径/读文件问题

## 用法

    python scripts/check_replay_data.py
    python scripts/check_replay_data.py --data 02-data/uav-inspection-backend/platedata
    python scripts/check_replay_data.py --strict-batch      # 批次不同即 FAIL（默认只 WARN）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys

MEDIA_PREFIX = "/api/v1/plate/media/"

OK, WARN, FAIL = "  ok ", " WARN", " FAIL"
_findings = []


def add(level, topic, msg):
    _findings.append((level, topic, msg))
    print("[%s] %-16s %s" % (level, topic, msg))


def load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def mp4_duration(path):
    """从 MP4 的 moov/mvhd 解出时长（秒）。解不出返回 None。"""
    with open(path, "rb") as fh:
        data = fh.read()
    for tag in (b"mvhd",):
        idx = data.find(tag)
        while idx != -1:
            p = idx + 4
            try:
                ver = data[p]
                if ver == 0:
                    timescale = struct.unpack(">I", data[p + 12:p + 16])[0]
                    duration = struct.unpack(">I", data[p + 16:p + 20])[0]
                elif ver == 1:
                    timescale = struct.unpack(">I", data[p + 20:p + 24])[0]
                    duration = struct.unpack(">Q", data[p + 24:p + 32])[0]
                else:
                    idx = data.find(tag, idx + 1)
                    continue
                if timescale:
                    return duration / float(timescale)
            except (IndexError, struct.error):
                pass
            idx = data.find(tag, idx + 1)
    return None


def media_path(data_dir, ref):
    """把 /api/v1/plate/media/x 映射到 data/media/x。"""
    if not ref:
        return None
    if ref.startswith(MEDIA_PREFIX):
        return os.path.join(data_dir, "media", ref[len(MEDIA_PREFIX):])
    if "://" in ref or ref.startswith("/"):
        return None                      # 外部 URL 或绝对路径，跳过
    return os.path.join(data_dir, ref)


def check_assets(data_dir, doc, name):
    refs = []
    video = doc.get("video") or {}
    for k in ("src", "poster"):
        if video.get(k):
            refs.append((name + ".video." + k, video[k]))
    for ph in doc.get("phases") or []:
        if ph.get("cover"):
            refs.append((name + ".phases[" + ph.get("key", "?") + "].cover", ph["cover"]))
    if doc.get("image"):
        refs.append((name + ".image", doc["image"]))
    bad = 0
    for label, ref in refs:
        p = media_path(data_dir, ref)
        if p is None:
            add(WARN, "素材", "%s = %s 是外部/绝对路径，跳过检查" % (label, ref))
            continue
        if not os.path.isfile(p):
            add(FAIL, "素材缺失", "%s → %s 不存在" % (label, os.path.relpath(p, data_dir)))
            bad += 1
        elif os.path.getsize(p) == 0:
            add(FAIL, "素材空文件", "%s → %s 是 0 字节" % (label, os.path.basename(p)))
            bad += 1
    if not bad and refs:
        add(OK, "素材", "%s 引用的 %d 个素材都在" % (name, len(refs)))


def check_video(tl, data_dir):
    video = tl.get("video") or {}
    src = video.get("src")
    p = media_path(data_dir, src) if src else None
    if not p or not os.path.isfile(p):
        return
    real = mp4_duration(p)
    claim = video.get("seconds")
    if real is None:
        add(WARN, "视频时长", "解不出 %s 的 mvhd，跳过时长比对" % os.path.basename(p))
        return
    if claim is None:
        add(WARN, "视频时长", "timeline.video.seconds 为空，实测 %.1f s" % real)
        return
    d = abs(real - float(claim))
    if d <= 2.0:
        add(OK, "视频时长", "实测 %.1f s ≈ 声明 %.1f s（差 %.1f s）" % (real, claim, d))
    else:
        add(FAIL, "视频时长", "实测 %.1f s，timeline 声明 %.1f s（差 %.1f s）"
            "——阶段条会 seek 到视频外" % (real, claim, d))
    frames, fps = video.get("frames"), video.get("fps")
    if frames and fps:
        calc = frames / float(fps)
        if abs(calc - float(claim)) > 1.0:
            add(WARN, "视频时长", "frames/fps=%.1f 与 seconds=%.1f 不一致" % (calc, claim))


def check_phases(tl):
    phases = tl.get("phases") or []
    if not phases:
        add(FAIL, "阶段", "phases 为空，阶段条无内容")
        return
    prev_t1 = None
    for ph in phases:
        k = ph.get("key", "?")
        t0, t1 = ph.get("t0"), ph.get("t1")
        if t0 is None or t1 is None:
            add(FAIL, "阶段", "%s 缺 t0/t1" % k)
            continue
        if t1 <= t0:
            add(FAIL, "阶段", "%s 的 t1(%s) <= t0(%s)" % (k, t1, t0))
        if ph.get("seconds") is not None and abs((t1 - t0) - ph["seconds"]) > 1:
            add(WARN, "阶段", "%s seconds=%s 与 t1-t0=%s 不一致" % (k, ph["seconds"], t1 - t0))
        if prev_t1 is not None and t0 < prev_t1:
            add(WARN, "阶段", "%s 的 t0(%s) 早于上一段结束(%s)，区间重叠" % (k, t0, prev_t1))
        prev_t1 = t1
    off = (tl.get("video") or {}).get("offset_s") or 0
    total = (tl.get("video") or {}).get("seconds") or 0
    if phases and total:
        last = phases[-1].get("t1") or 0
        # 收尾段末尾允许略超录屏末尾：录屏在脚本收尾（停 stage1、建车位图）之前就停了，
        # p10 实测差 9.3 s。超出本身不是错误，超出过多才是数据错（比如 offset 算反了）。
        over = last - (total + off)
        if over > 60:
            add(FAIL, "阶段",
                "末段 t1=%s 比录屏末尾 %.1f 晚 %.0f s，远超收尾容差（查 offset 是否算错）"
                % (last, total + off, over))
        elif over > 2:
            add(WARN, "阶段",
                "末段 t1=%s 比录屏末尾 %.1f 晚 %.0f s（收尾段未被录屏覆盖，属正常）"
                % (last, total + off, over))
        else:
            add(OK, "阶段", "%d 段，末段 t1=%s ≤ %.1f" % (len(phases), last, total + off))


def check_kpi(tl):
    kpi = tl.get("kpi") or {}
    rounds = kpi.get("rounds") or []
    if not rounds:
        add(FAIL, "KPI", "kpi.rounds 为空")
        return
    cap = sum(r.get("captured") or 0 for r in rounds)
    n_cap = kpi.get("n_captured")
    if n_cap is not None and cap < n_cap:
        add(WARN, "KPI", "各轮 captured 之和 %d < n_captured %d（轮次可能不全）" % (cap, n_cap))
    if kpi.get("inspect_round") is not None and kpi["inspect_round"] != len(rounds):
        add(WARN, "KPI", "inspect_round=%s 但 rounds 有 %d 条"
            % (kpi["inspect_round"], len(rounds)))
    n_cars, n_cap2 = kpi.get("n_cars"), kpi.get("n_captured")
    if n_cars and n_cap2 is not None:
        expect = round(n_cap2 / float(n_cars), 3)
        if kpi.get("c_cap") is not None and abs(expect - kpi["c_cap"]) > 0.002:
            add(WARN, "KPI", "c_cap=%s 与 n_captured/n_cars=%.3f 不一致"
                % (kpi["c_cap"], expect))
        else:
            add(OK, "KPI", "%d/%d，c_cap=%s，%d 轮" % (n_cap2, n_cars, kpi.get("c_cap"), len(rounds)))


def check_coverage(cv, slots_expected):
    area = cv.get("area") or {}
    site, car, obs = area.get("site_cells"), area.get("car_cells"), area.get("observable_cells")
    if None not in (site, car, obs) and site - car != obs:
        add(FAIL, "覆盖", "observable_cells %d != site(%d) - car(%d)" % (obs, site, car))
    for c in cv.get("coverage") or []:
        cells, ratio, mf = c.get("cells"), c.get("ratio"), c.get("min_frames")
        if None in (cells, ratio, obs) or not obs:
            continue
        calc = round(100.0 * cells / obs, 2)
        if abs(calc - float(ratio)) > 0.05:
            add(FAIL, "覆盖", "min_frames=%s 的 ratio=%s，但 cells/observable=%.2f"
                % (mf, ratio, calc))
    if cv.get("coverage"):
        add(OK, "覆盖", "%d 个阈值，分项自洽" % len(cv["coverage"]))
    slots = cv.get("slots") or []
    if slots_expected and len(slots) != slots_expected:
        add(WARN, "覆盖", "slots 有 %d 条，车位表是 %d 个" % (len(slots), slots_expected))
    elif slots:
        add(OK, "覆盖", "slots %d 条" % len(slots))


def check_rviz(cv, data_dir):
    """RViz 全流程录屏：文件在不在、时长与声明是否一致、分辨率对不对。"""
    rv = cv.get("rviz")
    if not rv:
        add(WARN, "RViz 录屏", "coverage.json 里没有 rviz 段（覆盖质量页不会显示录屏）")
        return
    src = rv.get("src") or ""
    p = media_path(data_dir, src)
    if not p or not os.path.isfile(p):
        add(FAIL, "RViz 录屏", "引用的文件不存在：%s" % src)
        return
    if os.path.getsize(p) < 100 * 1024:
        add(FAIL, "RViz 录屏", "文件只有 %d 字节，可疑" % os.path.getsize(p))
        return
    real = mp4_duration(p)
    claim = rv.get("seconds")
    if real is None:
        add(WARN, "RViz 录屏", "解不出 mvhd，跳过时长比对")
    elif claim is None:
        add(WARN, "RViz 录屏", "没写时长，实测 %.1f s" % real)
    elif abs(real - float(claim)) > 2.0:
        add(FAIL, "RViz 录屏", "实测 %.1f s，声明 %.1f s，差 %.1f s"
            % (real, float(claim), abs(real - float(claim))))
    else:
        add(OK, "RViz 录屏", "%.1f s / %dx%d（实测与声明一致）"
            % (real, rv.get("width") or 0, rv.get("height") or 0))
    poster = rv.get("poster")
    if poster:
        pp = media_path(data_dir, poster)
        if not pp or not os.path.isfile(pp):
            add(WARN, "RViz 录屏", "封面不存在：%s" % poster)
    if not rv.get("same_batch", True):
        add(WARN, "RViz 录屏", "录屏批次 %s 与覆盖质量数据不同批" % rv.get("run"))


def check_acceptance(ac, data_dir):
    vs = ac.get("vehicles") or []
    if not vs:
        add(FAIL, "验收", "vehicles 为空")
        return
    miss = []
    for v in vs:
        card = v.get("card")
        if card and not os.path.isfile(os.path.join(data_dir, "cards", card)):
            miss.append(card)
    if miss:
        add(FAIL, "验收", "缺缩略图 %d 张：%s" % (len(miss), ", ".join(miss[:5])))
    else:
        add(OK, "验收", "%d 辆车，缩略图齐全" % len(vs))
    bad = [v.get("car") for v in vs if not v.get("plate")]
    if bad:
        add(WARN, "验收", "以下车没有读出车牌：%s" % ", ".join(bad))


def check_batch(ac, tl, strict):
    batch = (ac.get("batch") or {})
    bdir = batch.get("batch_dir") or ""
    acc_run = os.path.basename(bdir.rstrip("/")) if bdir else None
    rep_run = (tl.get("mission") or {}).get("run")
    if not acc_run or not rep_run:
        add(WARN, "批次", "取不到批次名（acceptance.batch_dir=%r，timeline.mission.run=%r）"
            % (bdir, rep_run))
        return
    if acc_run == rep_run:
        add(OK, "批次", "两处都是 %s" % acc_run)
    else:
        lvl = FAIL if strict else WARN
        add(lvl, "批次",
            "不一致：巡检结果=%s（%s 起） vs 过程回放=%s（%s 起）"
            % (acc_run, batch.get("start"), rep_run,
               (tl.get("mission") or {}).get("started_at")))


def check_path(tl, data_dir):
    """path.json（第四格「路径与视点」）必须与 timeline 同批、同一条时间轴。

    2026-11-21 漏的正是这一项：timeline / coverage / acceptance 和全部素材都换成了
    p11 那批，path.json 还是 p12 的，于是回放走到第 5 分钟、第四格仍停在「第 0 步」
    —— 看上去像「这一格没跟时间同步」，实际是数据没换。

    判据取 `source` 里有没有 `timeline.mission.id`：这是唯一能把「同一条时间轴」
    验死的字段。只比对步数、时间范围都不行——两批都是 43 步、时间范围也重叠。
    """
    p = os.path.join(data_dir, "media", "path.json")
    if not os.path.isfile(p):
        add(WARN, "路径", "path.json 不存在（第四格会空着）")
        return
    try:
        doc = load(p)
    except Exception as e:                                    # noqa: BLE001
        add(FAIL, "路径", "解析失败：%s" % e)
        return
    steps = doc.get("steps") or []
    if not steps:
        add(FAIL, "路径", "steps 为空")
        return

    mid = ((tl or {}).get("mission") or {}).get("id")
    src = doc.get("source") or ""
    if not mid:
        add(WARN, "路径", "timeline 缺 mission.id，无法核对批次")
    elif mid in src:
        add(OK, "路径", "%d 步，批次与 timeline 一致（%s）" % (len(steps), mid))
    else:
        add(FAIL, "路径",
            "批次不一致：path.json 的 source 里没有 %s（source=%s）——第四格会拿旧批的"
            "时间戳去比，开头几分钟停在「第 0 步」" % (mid, src[:80]))

    ts = [s.get("t") or 0 for s in steps]
    if any(b < a for a, b in zip(ts, ts[1:])):
        add(FAIL, "路径", "t 不是单调递增（前端靠它逐条点亮）")
    else:
        add(OK, "路径", "t 单调递增 %.1f..%.1f s" % (ts[0], ts[-1]))

    vid = (tl or {}).get("video") or {}
    end = (vid.get("seconds") or 0) + (vid.get("offset_s") or 0)
    if end and ts[-1] > end + 1:
        add(WARN, "路径", "最后一步 %.1f s 超出视频覆盖 %.1f s，末尾几步看不到"
            % (ts[-1], end))


def default_data_dir():
    """数据根优先取 UAV_DATA_DIR（容器里是 /data），否则用仓库内 02-data。

    写死 02-data 在部署机上会指错目录：后端按 UAV_DATA_DIR 读，自检却查另一处，
    两边结论不一致时无法判断是谁错。
    """
    root = os.environ.get("UAV_DATA_DIR")
    if root:
        return os.path.join(os.path.expanduser(root), "uav-inspection-backend", "platedata")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "02-data", "uav-inspection-backend", "platedata")


def main(argv=None):
    ap = argparse.ArgumentParser(description="车牌回放数据自检")
    ap.add_argument("--data", default=default_data_dir())
    ap.add_argument("--slots", type=int, default=15, help="期望的车位数，0 表示不查")
    ap.add_argument("--strict-batch", action="store_true",
                    help="两处批次不同即 FAIL（默认只 WARN）")
    a = ap.parse_args(argv)

    data_dir = os.path.abspath(a.data)
    if not os.path.isdir(data_dir):
        print("数据目录不存在：%s" % data_dir)
        return 2
    print("自检数据目录：%s\n" % data_dir)

    docs = {}
    for name in ("timeline", "coverage", "acceptance"):
        p = os.path.join(data_dir, name + ".json")
        if not os.path.isfile(p):
            add(FAIL, name, "文件不存在")
            continue
        try:
            docs[name] = load(p)
            add(OK, name, "解析通过（%d 字节）" % os.path.getsize(p))
        except Exception as e:                                # noqa: BLE001
            add(FAIL, name, "解析失败：%s" % e)

    tl, cv, ac = docs.get("timeline"), docs.get("coverage"), docs.get("acceptance")

    print()
    if tl:
        check_assets(data_dir, tl, "timeline")
        check_video(tl, data_dir)
        check_phases(tl)
        check_kpi(tl)
        check_path(tl, data_dir)
    if cv:
        check_assets(data_dir, cv, "coverage")
        check_coverage(cv, a.slots)
        check_rviz(cv, data_dir)
    if ac:
        check_acceptance(ac, data_dir)
    if tl and ac:
        check_batch(ac, tl, a.strict_batch)

    n_fail = sum(1 for lv, _t, _m in _findings if lv == FAIL)
    n_warn = sum(1 for lv, _t, _m in _findings if lv == WARN)
    print("\n" + "=" * 72)
    print("结果：%d 项通过，%d 个 WARN，%d 个 FAIL"
          % (len(_findings) - n_fail - n_warn, n_warn, n_fail))
    if n_fail:
        print("FAIL 必须修：")
        for lv, t, m in _findings:
            if lv == FAIL:
                print("  - [%s] %s" % (t, m))
    print("=" * 72)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
