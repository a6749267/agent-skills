#!/usr/bin/env python3
"""把成片按片头/正文/片尾切成三段, 分别测 LUFS —— 精确定位每段实际电平。"""
import pathlib
import re
import subprocess
import sys

FF = "ffmpeg"
v = sys.argv[1]
OUT = pathlib.Path("/tmp/segcut")
OUT.mkdir(exist_ok=True)

# (名, 起, 止)
SEGS = [("intro", 0.0, 4.2), ("titlecard", 4.2, 6.12), ("body", 6.12, 205.48),
        ("outro", 205.48, 210.13)]

for name, a, b in SEGS:
    dst = OUT / f"{name}.wav"
    subprocess.run([FF, "-y", "-v", "error", "-ss", str(a), "-t", str(b - a),
                    "-i", v, "-vn", "-c:a", "pcm_s16le", str(dst)], check=True)
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", str(dst),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    I = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    P = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)
    m = re.search(r"mean_volume: (-?[\d.]+) dB",
                  subprocess.run([FF, "-v", "info", "-i", str(dst), "-af", "volumedetect",
                                 "-f", "null", "-"], capture_output=True, text=True).stderr)
    print(f"{name:>10} [{a:>6.1f}~{b:>6.1f}]  LUFS={I[-1] if I else '?':>8}  "
          f"Peak={P[-1] if P else '?':>7}  mean={m.group(1) if m else '?':>7}")
