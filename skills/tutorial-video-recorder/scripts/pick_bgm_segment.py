#!/usr/bin/env python3
"""BGM 片段选取 —— 一次算完所有选段指标, 别用"占比%"选曲(见 pitfalls G37)。

用法:
    python scripts/pick_bgm_segment.py <bgm.mp3> [--intro] [--outro] [--scan 0,26,196]

输出每个候选点的:
    冲击 dB     55-120Hz 功率密度 vs 宽带平均 → 越大越"砰"
    人声带 dB   300-3400Hz 功率密度 vs 宽带平均 → 越低越不压旁白
    首100ms     切入后 0~100ms 的 RMS → 判断是"满能量"还是"在渐入"
    预滚最优    切入点 = 重拍落点 − 0.25s（见 pitfalls G39）

⚠ 全部用**功率密度 + 等带宽归一**，不要用"频带能量占比%"——
   宽带音乐的占比恒 ≈ 带宽/奈奎斯特(3100/8000=38.75%)，与内容无关。
"""
import argparse
import subprocess
import sys
import tempfile
import wave

import numpy as np

SR = 44100
SR16 = 16000


def to_wav(src, start, dur, out, sr=SR16):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
                    "-t", f"{dur:.3f}", "-i", src, "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", out], check=True)
    w = wave.open(out, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    return x


def band_db(x, lo, hi, sr=SR16):
    """相对宽带平均功率密度的 dB（等带宽归一）。"""
    N = len(x)
    if N < 64:
        return -99.0
    P = (np.abs(np.fft.rfft(x)) ** 2) / N
    fr = np.fft.rfftfreq(N, 1.0 / sr)
    avg = P.sum() / (fr[-1] - fr[0])
    dens = P[(fr >= lo) & (fr < hi)].sum() / (hi - lo)
    return 10 * np.log10(dens / avg + 1e-12)


def onsets(src, t0, t1, hop=256, win=2048):
    """谱通量 onset 检测（别用 RMS 差分）。"""
    x = to_wav(src, t0, t1 - t0, "/tmp/_pk_beat.wav", sr=SR)
    w = np.hanning(win)
    frames = [np.abs(np.fft.rfft(x[i:i + win] * w))
              for i in range(0, len(x) - win, hop)]
    S = np.array(frames)
    flux = np.maximum(0, np.diff(S, axis=0)).sum(axis=1)
    flux /= flux.max() + 1e-9
    k = 9
    fs = np.convolve(flux, np.ones(k) / k, mode="same")
    peaks = []
    for i in range(2, len(fs) - 2):
        if fs[i] == max(fs[max(0, i - 8):i + 9]) and fs[i] > 0.12:
            peaks.append((float(fs[i]), t0 + (i + 1) * hop / SR))
    peaks.sort(reverse=True)
    return peaks


def rms_db(x):
    return 20 * np.log10(float(np.sqrt((x ** 2).mean())) + 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bgm")
    ap.add_argument("--scan", default="26,196,231",
                    help="逗号分隔的候选切入点(秒)")
    ap.add_argument("--scan-around", type=float, default=0.0,
                    help="在该秒附近自动找重拍并测预滚最优(0=关闭)")
    ap.add_argument("--preroll", type=float, default=0.25,
                    help="预滚秒数：切入点 = 落点 − preroll（见 pitfalls G39）")
    ap.add_argument("--len", dest="seglen", type=float, default=24.36)
    a = ap.parse_args()

    print("=" * 78)
    print("候选点指标（冲击/���声带/首100ms）")
    print("=" * 78)
    print(f"{'切入点':>9} {'冲击dB':>9} {'人声带dB':>10} {'首100ms':>9} {'后1s':>9}  判断")
    print("-" * 78)
    results = []
    for s in [float(v) for v in a.scan.split(",") if v.strip()]:
        x = to_wav(a.bgm, s, a.seglen, "/tmp/_pk_win.wav")
        imp = band_db(x, 55, 120)
        voice = band_db(x, 300, 3400)
        x44 = to_wav(a.bgm, s, 1.6, "/tmp/_pk_w44.wav", sr=SR)
        first100 = rms_db(x44[:SR // 10])
        rest = rms_db(x44[int(SR * 0.3):int(SR * 1.3)])
        verdict = "✅ 人声带干净" if voice < -4 else ("⚠️ 偏亮" if voice < -2 else "❌ 人声带突出")
        print(f"{s:9.3f} {imp:+9.2f} {voice:+10.2f} {first100:9.2f} {rest:9.2f}  {verdict}")
        results.append((s, imp, voice, first100))

    if a.scan_around > 0:
        print()
        print("=" * 78)
        print(f"重拍检测 + 预滚扫描（{a.scan_around:.1f}s 附近，切入 = 落点 − {a.preroll}s）")
        print("=" * 78)
        pk = onsets(a.bgm, a.scan_around - 12, a.scan_around + 12)
        print("  最强重拍:")
        for strength, t in pk[:6]:
            print(f"    t={t:7.3f}s  强度={strength:.3f}")
        print()
        print(f"{'落点':>9} {'切入':>9} {'首100ms':>9} {'后1s':>9} {'差值':>8}")
        print("-" * 78)
        best = None
        for strength, t in pk[:8]:
            cut = t - a.preroll
            if cut < 0:
                continue
            x44 = to_wav(a.bgm, cut, 1.6, "/tmp/_pk_cut.wav", sr=SR)
            f100 = rms_db(x44[:SR // 10])
            rest = rms_db(x44[int(SR * 0.3):int(SR * 1.3)])
            delta = f100 - rest
            print(f"{t:9.3f} {cut:9.3f} {f100:9.2f} {rest:9.2f} {delta:+8.2f}")
            if best is None or delta > best[0]:
                best = (delta, cut, t)
        if best:
            print()
            print(f"  ✅ 最佳: 落点 {best[2]:.3f}s → 切入 {best[1]:.3f}s"
                  f"（首100ms 比后续1s 高 {best[0]:+.2f} dB）")
            x = to_wav(a.bgm, best[1], a.seglen, "/tmp/_pk_best.wav")
            print(f"     该点 人声带 {band_db(x, 300, 3400):+.2f} dB"
                  f" / 冲击 {band_db(x, 55, 120):+.2f} dB")
            print(f"     ⚠ 切入 {best[1]:.3f} 还要复验人声带/冲击, 两者冲突时以人声带为准")

    print()
    print("判据: 人声带 < −4dB 优先 / 冲击越大越好 / 首100ms 应接近满能量(不是爬升)")


if __name__ == "__main__":
    main()
