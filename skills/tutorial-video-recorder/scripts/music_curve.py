#!/usr/bin/env python3
"""曲子结构分析: 1s 粒度能量/低频/高频曲线, 用于挑选"片头冲击段/正文驱动段/片尾收尾段"。

输出:
  每 1s 的: 总能量(dB归一) / 低频能量 / 高频能量 / 是否 onset 密集
  以及自动推荐:
    - intro段:  能量最高且有 onset 密集的连续区间(适合当片头冲击)
    - body段:   能量平稳偏高的连续区间(适合正文驱动)
    - outro段:  能量高 + 位于曲子后段的区间(适合片尾收尾)
"""
import json
import subprocess
import sys

import numpy as np

SR = 22050
HOP = 0.01
WIN = 0.05


def load(path, start, dur):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur),
         "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def curve(path):
    x = load(path, 0, 400)
    hop_i, win_i = int(SR * HOP), int(SR * WIN)
    n = (len(x) - win_i) // hop_i
    fr = x[:n * hop_i].reshape(n, hop_i) * np.hanning(hop_i)
    spec = np.abs(np.fft.rfft(fr, n=win_i, axis=1)).T
    freqs = np.fft.rfftfreq(win_i, 1 / SR)
    rms = np.sqrt((x[:n * hop_i].reshape(n, hop_i) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    dbn = (db - db.min()) / (db.max() - db.min() + 1e-9)

    def band(lo, hi):
        sel = (freqs >= lo) & (freqs < hi)
        s = spec[sel]
        s = s / (s.max() + 1e-9)
        return np.power(s, 0.45).mean(axis=0)

    low = band(20, 200)
    high = band(4000, 16000)
    mid = band(200, 4000)
    # onset 强度(每秒)
    flux = np.maximum(0.0, np.diff(spec, axis=1)).sum(0)
    per = int(1.0 / HOP)
    onset = [float(flux[i * per:(i + 1) * per].mean()) for i in range(len(flux) // per)]
    # 每秒聚合
    out = []
    for i in range(len(onset)):
        a = i * per
        b = min(a + per, len(dbn))
        out.append({"t": i, "e": float(dbn[a:b].mean()),
                    "low": float(low[a:b].mean()), "high": float(high[a:b].mean()),
                    "mid": float(mid[a:b].mean()), "onset": onset[i]})
    return out


def win_score(c, a, b, key):
    """区间得分"""
    seg = c[a:b]
    if not seg:
        return 0.0
    return float(np.mean([s[key] for s in seg]))


def best_intro(c, need):
    """能量高 + onset 密集 的连续段"""
    best = (-1, 0)
    for a in range(0, len(c) - need):
        e = win_score(c, a, a + need, "e")
        o = win_score(c, a, a + need, "onset")
        lo = win_score(c, a, a + need, "low")
        s = e * 2 + o * 1.5 + lo
        if s > best[0]:
            best = (s, a)
    return best[1]


def best_body(c, need):
    """能量平稳(方差小)且偏高 的连续段"""
    best = (-1, 0)
    for a in range(0, len(c) - need, 2):
        seg = c[a:a + need]
        e = np.mean([s["e"] for s in seg])
        v = np.std([s["e"] for s in seg])
        lo = np.mean([s["low"] for s in seg])
        s = e * 2 - v * 3 + lo
        if s > best[0]:
            best = (s, a)
    return best[1]


def main(path):
    c = curve(path)
    json.dump(c, open("/tmp/music_curve.json", "w"))
    print(f"总长 {len(c)}s")
    print("\n=== 每 5s 概览 (t: 能量 低 高 onset) ===")
    for i in range(0, len(c), 5):
        s = c[i]
        bar = "█" * int(s["e"] * 30)
        print(f"{s['t']:>4}s {bar:<30} e={s['e']:.2f} low={s['low']:.2f} "
              f"high={s['high']:.2f} onset={s['onset']:.3f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/music/623.mp3")
