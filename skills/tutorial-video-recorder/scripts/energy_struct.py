#!/usr/bin/env python3
"""能量包络结构分析: 找 build-up(爬升) 和 drop(落点)。

墙纸音乐 = 全程能量平坦 → 不适合有片头/片尾/章节的教程视频。
好配乐 = 前段低 → 中段爬升 → 高潮有落点 → 收尾不突兀。
"""
import json
import subprocess
import sys

import numpy as np

SR = 22050
HOP = 0.01
WIN = 0.05


def analyze(path, start=0.0, dur=90.0):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur),
         "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if len(x) < SR * 5:
        return None
    hop_i, win_i = int(SR * HOP), int(SR * WIN)
    n = (len(x) - win_i) // hop_i
    fr = x[:n * hop_i].reshape(n, hop_i) * np.hanning(hop_i)
    spec = np.abs(np.fft.rfft(fr, n=win_i, axis=1)).T   # n=win_i → 分辨率 = SR/win_i = 20Hz
    freqs = np.fft.rfftfreq(win_i, 1 / SR)              # ← win_i 决定分辨率
    rms = np.sqrt((x[:n * hop_i].reshape(n, hop_i) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    dbn = (db - db.min()) / (db.max() - db.min() + 1e-9)

    # 低频能量(kick/bass)
    low = spec[(freqs >= 20) & (freqs < 200)]
    lown = (low / (low.max() + 1e-9)) if low.size else np.zeros(spec.shape[1])
    low_db = 20 * np.log10(lown + 1e-6)
    low_db = (low_db - low_db.min()) / (low_db.max() - low_db.min() + 1e-9)

    q = np.array_split(dbn, 4)
    seg = [float(x.mean()) for x in q]
    # build: 后段 - 前段
    build = seg[3] - seg[0]
    # 最大爬升斜率(每秒)
    win = int(3.0 / HOP)
    smooth = np.convolve(dbn, np.ones(win) / win, mode="same")
    slope = float(np.max(np.diff(smooth)) / HOP)
    # 起伏次数(dynamic swings)
    d = np.diff(smooth)
    swings = int(np.sum((d[:-1] * d[1:]) < 0) / max(1, len(d) / 200))
    # 低频驱动强度
    kick = float(np.mean(np.abs(np.diff(low_db)) > 0.05))
    return {"seg": seg, "build": build, "slope": slope, "swings": swings,
            "kick": kick, "lowmax": float(low_db.max())}


if __name__ == "__main__":
    ids = sys.argv[1:]
    out = {}
    print(f"{'id':>5} {'seg1':>5} {'seg2':>5} {'seg3':>5} {'seg4':>5} "
          f"{'build':>7} {'slope':>7} {'swings':>6} {'kickdrv':>7}")
    for t in ids:
        r = analyze(f"/tmp/music/{t}.mp3")
        if not r:
            continue
        out[t] = r
        s = r["seg"]
        print(f"{t:>5} {s[0]:5.2f} {s[1]:5.2f} {s[2]:5.2f} {s[3]:5.2f} "
              f"{r['build']:+7.3f} {r['slope']:7.3f} {r['swings']:6d} {r['kick']:7.3f}")
    json.dump(out, open("/tmp/energy_struct.json", "w"), indent=1)
