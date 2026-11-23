#!/usr/bin/env python3
"""诚实地验证三段动态:
  stem = 口播权重置 0 的同一张图 → 音乐在成片里的实际贡献
  比较片头 / 标题卡 / 正文 / 片尾 的相对电平
"""
import re
import subprocess
import sys

S = sys.argv[1]          # 纯音乐贡献 wav
V = sys.argv[2]          # 成片 mp4


def lufs(path, t, dur=1.8):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-t", str(dur),
                    "-i", path, "/tmp/_m.wav"], check=True)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", "/tmp/_m.wav",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    I = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    return float(I[-1]) if I else 0.0


PTS = [
    (0.5, "片头"), (4.5, "标题卡0"), (25.6, "标题卡1"), (46.6, "标题卡2"),
    (73.2, "标题卡3"), (104.8, "标题卡4"), (133.5, "标题卡5"), (159.4, "标题卡6"),
    (183.1, "标题卡7"),
    (10, "正文1"), (35, "正文2"), (55, "正文3"), (85, "正文4"), (115, "正文5"),
    (145, "正文6"), (170, "正文7"), (195, "正文8"),
    (206, "片尾"),
]

print(f"{'区间':<10} {'音乐贡献':>9} {'成片':>9}")
music = {}
for t, label in PTS:
    m = lufs(S, t)
    v = lufs(V, t)
    music[label] = m
    print(f"{label:<10} {m:>9.1f} {v:>9.1f}")

body = [music[k] for k in music if k.startswith("正文")]
cards = [music[k] for k in music if k.startswith("标题卡")]
b = sum(body) / len(body)
c = sum(cards) / len(cards)
print(f"\n=== 音乐贡献的相对关系 ===")
print(f"  片头   {music['片头']:>7.1f} LUFS  (比正文 {music['片头'] - b:+.1f} dB)")
print(f"  标题卡 {c:>7.1f} LUFS  (比正文 {c - b:+.1f} dB)")
print(f"  正文   {b:>7.1f} LUFS  (基准)")
print(f"  片尾   {music['片尾']:>7.1f} LUFS  (比正文 {music['片尾'] - b:+.1f} dB)")
