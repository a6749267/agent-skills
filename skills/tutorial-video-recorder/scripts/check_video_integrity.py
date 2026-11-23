#!/usr/bin/env python3
"""成片完整性体检 —— 交付前一道闸, 把 4 类「静默失败」一次性查完。

🚨 这四类失败的共同点: **不报错、规格看起来完全正常、自动化验收全过**。
   只有像素级/时间戳级体检才能抓到。

1. 帧间隔异常（容器推断成 50fps）
   → ffprobe 拿 packet pts, **必须先排序**（B 帧重排 → 输出顺序≠显示顺序,
     不排序会算出 -0.08s 这种假异常）, 再 diff。
   → 期望: 全部间隔 == 1/fps, min==max==中位。

2. 字幕方向错误（横排变竖排一列）
   → 成因: render_png(lines) 收到的是**字符串**而非行列表 → len("...")=字数
     → 每个字单独一行 → 字幕从 y=195 竖排到 y=990。
   → 检测: 逐行统计白色像素的**最长连续段**。
     横排 → 某行有几百 px 的连续白段; 竖排 → 每行只有 ~40px(单字宽)。

3. 字幕覆盖率
   → 检测带位置**必须实测**（margin_v=60 → 单行 y∈[904,944]、双行 [963,1003]
     → 检测带取 [880,1015])。算错会得到「100%」或「36%」的假数。
   → 检测带**不能框到技术图自身的浅色元素** —— 否则假 100%。

4. 视频流条数
   → 有字幕时必须只有 1 条（两条 → 播放器放第一条 = 白烧）。

用法:
    python check_video_integrity.py 成片.mp4
    python check_video_integrity.py 成片.mp4 --band 880,1015 --fps 25
退出码: 0 = 全过, 1 = 有 BLOCK
"""
import argparse
import pathlib
import subprocess
import sys

import numpy as np

FF = "ffmpeg"
FP = "ffprobe"


def probe_packets(path):
    cmd = [FP, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    pts = []
    for line in out.strip().split("\n"):
        if not line or line == "N/A":
            continue
        try:
            pts.append(float(line.split(",")[0]))
        except ValueError:
            pass
    return sorted(pts)          # 🚨 必须排序: B 帧重排


def probe_streams(path):
    cmd = [FP, "-v", "error", "-show_entries", "stream=index,codec_type,width,height,"
           "r_frame_rate,nb_frames", "-of", "csv=p=0", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return [l for l in out.strip().split("\n") if l]


def check_frames(path, fps):
    pts = probe_packets(path)
    if len(pts) < 10:
        return ["BLOCK", f"视频包太少({len(pts)}) —— 文件可能损坏"]
    d = np.diff(np.array(pts))
    target = 1.0 / fps
    bad = np.where(np.abs(d - target) > 1e-4)[0]
    if len(bad) == 0:
        return ["OK", f"{len(pts)} 帧, 间隔全 = {target:.4f}s (min={d.min():.4f} max={d.max():.4f})"]
    detail = ", ".join(f"#{i+1}@{pts[i]:.3f}s 间隔{d[i]:.4f}s" for i in bad[:8])
    return ["BLOCK", f"{len(bad)}/{len(d)} 处帧间隔异常(期望 {target:.4f}s): {detail}"]


def load_gray_band(path, y0, y1, w=1920):
    cmd = [FF, "-v", "error", "-i", str(path),
           "-vf", f"crop={w}:{y1-y0}:0:{y0},format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    band = y1 - y0
    n = len(raw) // (w * band)
    raw = raw[: n * w * band]
    return np.frombuffer(raw, dtype=np.uint8).reshape(n, band, w), n


def check_subtitle_direction(path, y0, y1):
    """横排 vs 竖排: 逐帧看白色像素的最长连续段。"""
    arr, n = load_gray_band(path, y0, y1)
    if n < 5:
        return ["WARN", "帧数太少, 跳过方向检测"]
    vertical = 0
    samples = []
    for fi in range(0, n, max(1, n // 12)):
        m = arr[fi] >= 200
        if not m.any():
            continue
        cols = m.any(axis=0)
        best = cur = 0
        for v in cols:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        samples.append(best)
        if best < 120:          # 横排一行文字的连续白段应 >120px
            vertical += 1
    if not samples:
        return ["WARN", "检测带内没找到白色像素 —— 检测带位置可能算错了"]
    if vertical >= len(samples) * 0.6:
        return ["BLOCK", f"字幕疑似**竖排**(每行最长白段 <120px, {vertical}/{len(samples)} 帧) —— "
                         f"render_png 收到的是字符串而不是行列表, 要先 wrap_two_lines()"]
    return ["OK", f"字幕横排正常(最长白段中位 {int(np.median(samples))}px)"]


def check_coverage(path, y0, y1, fps):
    arr, n = load_gray_band(path, y0, y1)
    if n == 0:
        return ["BLOCK", "抽帧失败"]
    white = (arr >= 200).sum(axis=(1, 2))
    has = white > 30
    cover = has.sum() / n * 100
    gaps = []
    i = 0
    while i < n:
        if not has[i]:
            j = i
            while j < n and not has[j]:
                j += 1
            gaps.append((i, j))
            i = j
        else:
            i += 1
    gap_txt = ", ".join(f"{s/fps:.1f}~{e/fps:.1f}s" for s, e in gaps[:6])
    if cover >= 97:
        return ["OK", f"字幕覆盖率 {cover:.2f}% ({has.sum()}/{n} 帧)"
                + (f", 缺口 {gap_txt}" if gaps else "")]
    if cover >= 90:
        return ["WARN", f"字幕覆盖率 {cover:.2f}% —— 低于 97%, 缺口: {gap_txt}"]
    return ["BLOCK", f"字幕覆盖率 {cover:.2f}% —— 低于 90% 门槛, 缺口: {gap_txt}"]


def check_stream_count(path):
    v = [l for l in probe_streams(path) if ",video," in l]
    a = [l for l in probe_streams(path) if ",audio," in l]
    if len(v) > 1:
        return ["BLOCK", f"有 {len(v)} 条视频流 —— 播放器只放第一条, 字幕/框等于白烧"]
    if not a:
        return ["BLOCK", "没有音频流"]
    return ["OK", f"1 条视频流 + {len(a)} 条音频流"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--band", default="880,1015",
                    help="字幕检测带 y0,y1 (默认 880,1015, 对应 margin_v=60)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--min-cover", type=float, default=90.0)
    a = ap.parse_args()

    p = pathlib.Path(a.video)
    if not p.exists():
        print(f"❌ 文件不存在: {p}")
        sys.exit(1)
    y0, y1 = [int(x) for x in a.band.split(",")]

    print(f"=== 成片完整性体检: {p.name} ===")
    print(f"    字幕检测带 y∈[{y0},{y1})  fps={a.fps}\n")

    results = [
        ("流结构", check_stream_count(p)),
        ("帧间隔", check_frames(p, a.fps)),
        ("字幕方向", check_subtitle_direction(p, y0, y1)),
        ("字幕覆盖", check_coverage(p, y0, y1, a.fps)),
    ]
    blocked = False
    for name, (level, msg) in results:
        icon = {"OK": "✅", "WARN": "⚠️ ", "BLOCK": "❌"}[level]
        print(f"{icon} {name}: {msg}")
        if level == "BLOCK":
            blocked = True

    print()
    print("❌ 有 BLOCK 项 —— 不要交付" if blocked else "✅ 全部通过")
    sys.exit(1 if blocked else 0)


if __name__ == "__main__":
    main()
