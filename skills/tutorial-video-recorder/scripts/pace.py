#!/usr/bin/env python3
r"""节奏体检 —— 用帧差量化"画面是不是死的"。**别凭感觉判断视频节奏。**

用法:
    python pace.py video.mp4                # 体检
    python pace.py a.mp4 b.mp4 --compare     # 对比两个视频

为什么需要这个:
    录屏类视频的通病是**死画面** —— 界面静止 20 秒不动一个像素,
    观众 3 秒划走。但人眼看"感觉有点闷", 说不出差在哪。
    帧差能给出硬指标:
        · 变化强度(帧差均值)  —— 越大画面越"活"
        · 变化帧/分钟         —— 视觉事件密度
        · 最长静止段          —— 致命指标, 观众划走的时点
        · 硬切点/分钟         —— 章节切换密度

    实测参考值(B站同类视频):
        技术开源项目 89s     : 强度 19.35 | 54.6 次/分 | 硬切 8 个
        ComfyUI 整合包 1210s : 强度  6.21 | 45.2 次/分 | 硬切 34 个
        教程型录屏(改造前)  : 强度  0.87 | 14.1 次/分 | 硬切  0 个  ← 死画面
        目标区间             : 强度 ≥ 3.0 | ≥ 30 次/分 | 最长静止 ≤ 6s

    ⚠ 阈值不是审美标准, 是**下限**: 低于这个区间观众会走。
      死画面段越短, 观感越好 —— 上限由"闪得让人晕"决定, 不设死线。
"""
import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

FF = "ffmpeg"
FFP = "ffprobe"


def probe_duration(video):
    r = subprocess.run([FFP, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def scene_cuts(video, thr=0.2):
    r = subprocess.run([FF, "-hide_banner", "-i", str(video), "-filter:v",
                        f"select='gt(scene,{thr})',metadata=print:file=-",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    out = []
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try:
                out.append(float(line.split("pts_time:")[1].split()[0]))
            except Exception:
                pass
    return out


def frame_activity(video, workdir, fps=1, width=480):
    """抽 1fps 帧算相邻帧差 → 变化强度时间线。"""
    from PIL import Image
    import numpy as np
    d = pathlib.Path(workdir)
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*.jpg"):
        f.unlink()
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps}", "-q:v", "3", str(d / "f%05d.jpg")],
                   check=True)
    files = sorted(d.glob("f*.jpg"))
    prev = None
    diffs = []
    for f in files:
        im = Image.open(f).convert("L")
        w, h = im.size
        arr = np.asarray(im.resize((width, int(width * h / w))), dtype="float32")
        if prev is not None:
            diffs.append(float(np.abs(arr - prev).mean()))
        prev = arr
    return diffs


def analyze(video, workdir):
    dur = probe_duration(video)
    diffs = frame_activity(video, workdir)
    n = len(diffs)
    cuts = scene_cuts(video)
    act = [i for i, v in enumerate(diffs) if v > 1.0]

    # 连续静止段(变化阈值以下的最长连续区间)
    segs = []
    cur = None
    for i, v in enumerate(diffs):
        if v <= 1.0:
            cur = [i, i] if cur is None else [cur[0], i]
        else:
            if cur:
                segs.append(tuple(cur))
            cur = None
    if cur:
        segs.append(tuple(cur))
    longest_static = max((b - a + 1 for a, b in segs), default=0)

    res = dict(
        path=str(video), dur=dur, n=n,
        mean=float(sum(diffs) / len(diffs)) if diffs else 0.0,
        max=float(max(diffs)) if diffs else 0.0,
        act_per_min=(len(act) / n * 60) if n else 0.0,
        cuts=len(cuts),
        cuts_per_min=(len(cuts) / (dur / 60)) if dur else 0.0,
        longest_static=longest_static,
        static_segs=len(segs),
        static_ratio=(sum(b - a + 1 for a, b in segs) / n * 100) if n else 0.0,
    )
    return res


def report(r):
    print(f"\n{'=' * 64}")
    print(f"节奏体检: {pathlib.Path(r['path']).name}")
    print("=" * 64)
    print(f"  时长              {r['dur']:.1f}s")
    print(f"  变化强度(均值)    {r['mean']:.2f}      目标 ≥ 3.0")
    print(f"  变化强度(峰值)    {r['max']:.1f}")
    print(f"  变化帧/分钟       {r['act_per_min']:.1f}      目标 ≥ 30")
    print(f"  硬切点            {r['cuts']} 个 ({r['cuts_per_min']:.1f}/分)")
    print(f"  最长静止段        {r['longest_static']}s      目标 ≤ 6s")
    print(f"  静止画面占比      {r['static_ratio']:.1f}%     目标 ≤ 25%")
    ok = (r["mean"] >= 3.0 and r["act_per_min"] >= 30
          and r["longest_static"] <= 6 and r["static_ratio"] <= 25)
    print(f"\n  {'✅ 达标' if ok else '❌ 未达标 —— 画面太死, 观众会划走'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()
    results = []
    with tempfile.TemporaryDirectory() as td:
        for i, v in enumerate(a.videos):
            r = analyze(v, pathlib.Path(td) / f"w{i}")
            results.append(r)
            report(r)
    if a.compare and len(results) > 1:
        print(f"\n{'=' * 64}\n对比\n{'=' * 64}")
        keys = [("mean", "变化强度"), ("act_per_min", "变化帧/分"),
                ("cuts_per_min", "硬切/分"), ("longest_static", "最长静止s"),
                ("static_ratio", "静止占比%")]
        print(f"{'指标':<14}" + "".join(f"{pathlib.Path(r['path']).name[:16]:>18}"
                                       for r in results))
        for k, label in keys:
            print(f"{label:<14}" + "".join(f"{r[k]:>18.2f}" for r in results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
