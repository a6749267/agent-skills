#!/usr/bin/env python3
"""逐秒抽帧测平均电平(volumedetect), 验证配乐混入 + 闪避生效。"""
import re
import subprocess
import sys


def mean_db(path, ts, dur=0.9):
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(ts), "-t", str(dur), "-i", path,
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", out)
    return float(m.group(1)) if m else None


def main(video, silent, points):
    print(f"{'t':>5} {'成片dB':>8} {'旁白dB':>8} {'差':>7}  区间")
    for t, label in points:
        a = mean_db(video, t)
        b = mean_db(silent, t)
        if a is None or b is None:
            print(f"{t:>5}  (测不到)")
            continue
        print(f"{t:>5} {a:>8.1f} {b:>8.1f} {a-b:>+7.1f}  {label}")


if __name__ == "__main__":
    pts = [(2, "片头(无旁白→音乐应明显更响)"),
           (5, "标题卡(无旁白)"),
           (10, "第1段旁白(音乐应被压低)"),
           (35, "第2段旁白"), (55, "第3段旁白"), (85, "第4段旁白"),
           (115, "第5段旁白"), (145, "第6段旁白"), (170, "第7段旁白"),
           (195, "第8段旁白"),
           (207, "片尾(无旁白→音乐应明显更响)")]
    main(sys.argv[1], sys.argv[2], pts)
