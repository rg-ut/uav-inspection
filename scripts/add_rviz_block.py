"""把 RViz 全流程录屏的元数据写进 coverage.json 的 `rviz` 段。

为什么单独一步而不是塞进 `v4e_coverage_report.py`：那个脚本算的是覆盖率，
不认识视频；而视频的时长/分辨率必须**从成品文件实测**，不能从参数抄。
时长本脚本自己解 mvhd；分辨率由调用方用 ffprobe 量好传进来（避免在两种
box 偏移上猜）。

用法：
    python scripts/add_rviz_block.py \
        --coverage 02-data/uav-inspection-backend/platedata/coverage.json \
        --video    02-data/uav-inspection-backend/platedata/media/rviz_process.mp4 \
        --poster   02-data/uav-inspection-backend/platedata/media/rviz_process_poster.jpg \
        --width 1920 --height 1080 --run p11_accept_20261116_113536 --same-batch
"""
import argparse
import json
import os
import struct

NOTE = ("橙色点云是障碍层 /scene_cloud：深度相机逐帧点云按 0.15 m 栅格累积，"
        "再与车体语义 OBB 融合，得到可飞的占据图；左下两块面板是同一时刻的 RGB 与 "
        "16UC1 深度图。检测不靠可见光本身，靠的是这套「点云 → 栅格 → 障碍层」的融合结果。")


def mp4_duration(path):
    """从 moov/mvhd 解出时长（秒）。解不出返回 None。"""
    with open(path, "rb") as fh:
        data = fh.read(4 * 1024 * 1024)
    idx = data.find(b"mvhd")
    if idx < 0:
        return None
    ver = data[idx + 4]
    try:
        if ver == 1:
            timescale = struct.unpack(">I", data[idx + 24:idx + 28])[0]
            duration = struct.unpack(">Q", data[idx + 28:idx + 36])[0]
        else:
            timescale = struct.unpack(">I", data[idx + 16:idx + 20])[0]
            duration = struct.unpack(">I", data[idx + 20:idx + 24])[0]
    except struct.error:
        return None
    return duration / float(timescale) if timescale else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--poster", default="")
    ap.add_argument("--run", required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--same-batch", action="store_true")
    ap.add_argument("--offset-s", type=float, default=0.0,
                    help="录屏起点相对任务起跑的秒数（来自 timeline.video.offset_s）；>0 时在 note 里披露未覆盖的前段")
    ap.add_argument("--src-url", default="/api/v1/plate/media/rviz_process.mp4")
    ap.add_argument("--poster-url", default="/api/v1/plate/media/rviz_process_poster.jpg")
    a = ap.parse_args()

    dur = mp4_duration(a.video)
    if dur is None:
        raise SystemExit("读不出 mvhd：%s" % a.video)

    doc = json.load(open(a.coverage, encoding="utf-8"))
    has_poster = bool(a.poster) and os.path.isfile(a.poster)
    note = NOTE
    if a.offset_s and a.offset_s > 0:
        # 录屏是人在任务起跑后才点开录的，前段天然缺；不写清楚就会被当成「整轮都在里面」。
        # 只说「缺了多少秒」，不说「缺的是 SURVEY」——p11（2026-11-21）那次 offset 174 s，
        # 而 SURVEY 到第 403 s 才结束，前半段在画面里，写死成「SURVEY 不在画面内」就是错的。
        note = ("录屏自任务起跑后第 %.0f s 开始（此前 %.0f s 不在画面内）。"
                % (a.offset_s, a.offset_s)) + NOTE
    doc["rviz"] = {
        "src": a.src_url,
        "poster": a.poster_url if has_poster else "",
        "seconds": round(dur, 1),
        "width": a.width,
        "height": a.height,
        "fps": a.fps,
        "run": a.run,
        "note": note,
        "offset_s": round(float(a.offset_s or 0.0), 1),
        "same_batch": bool(a.same_batch),
    }
    # newline="\n"：Windows 上默认会写 CRLF，仓库里别的 JSON 都是 LF
    with open(a.coverage, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    print("已写入 rviz 段：%.1f s  %dx%d  fps=%.0f  poster=%s  run=%s"
          % (dur, a.width, a.height, a.fps, "有" if has_poster else "无", a.run))


if __name__ == "__main__":
    main()
