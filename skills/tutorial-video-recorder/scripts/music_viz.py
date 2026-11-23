#!/usr/bin/env python3
"""生成**高信息密度**的音乐分析图, 供多模态模型判断曲风。

🚨🚨🚨 **STFT 窗口长度必须 >> hop** (低频分析的命门):
    第一版 hop=10ms 同时当 FFT 长度 → 频率分辨率 = SR/220 = **100Hz/bin**
    → 20-200Hz 只有 **1 个 bin** → LOW 面板渲染成空, kick 指标恒为 0。
    子代理独立复核时识破了这是渲染 bug 不是"低频为空"。
    正确: NFFT=2048 (分辨率 10.8Hz), HOP=512。

✅ 图里包含 5 个子图, 每个坐标都可读:
    ① 全频段频谱 (对数频率轴, 青色竖线=onset)
    ② 低频 20-200Hz  ← 底鼓/贝斯有没有, 是否"落地"
    ③ 中频 200-2kHz  ← 旋律/人声主体
    ④ 高频 2k-16kHz  ← 镲片/合成器亮度
    ⑤ 能量包络 + 4 段结构 + onset 标记
"""
import pathlib
import subprocess
import sys

import numpy as np

SR = 22050
NFFT = 2048
HOP = 512


def build(mp3, out_png, start=0.0, dur=70.0, title=""):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur),
         "-i", str(mp3), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if len(x) < SR * 3:
        raise SystemExit("too short")

    n_frames = 1 + (len(x) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = x[idx] * np.hanning(NFFT)[None, :]
    spec = np.abs(np.fft.rfft(frames, axis=1)).T
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    hop_s = HOP / SR
    t_axis = np.arange(spec.shape[1]) * hop_s

    # RMS 包络 (50ms)
    w_i = int(SR * 0.05)
    n = (len(x) - w_i) // w_i
    rms = np.sqrt((x[:n * w_i].reshape(n, w_i) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    dbn = (db - db.min()) / (db.max() - db.min() + 1e-9)

    # onset
    flux = np.maximum(0.0, np.diff(spec, axis=1)).sum(0)
    if flux.max() > 0:
        flux = flux / flux.max()
    thr = np.percentile(flux, 90)
    peaks = [i for i in range(2, len(flux) - 2)
             if flux[i] > thr and flux[i] == max(flux[i - 2:i + 3])]
    pruned = []
    for p in peaks:
        if not pruned or (p - pruned[-1]) * hop_s > 0.08:
            pruned.append(p)
    onset_t = np.array(pruned) * hop_s

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(16, 14), dpi=100)
    gs = fig.add_gridspec(3, 2, hspace=0.45, wspace=0.16,
                          height_ratios=[1.5, 1, 1])

    def band(ax, lo, hi, label, cmap):
        sel = (freqs >= lo) & (freqs < hi)
        n_bins = int(sel.sum())
        if n_bins < 2:
            ax.set_title(f"{label}: (no bins)")
            ax.set_xlabel("time (s)")
            return
        s = spec[sel]
        s = s / (s.max() + 1e-9)
        s = np.power(s, 0.45)
        ax.imshow(s, aspect="auto", origin="lower", cmap=cmap,
                  extent=[t_axis[0], t_axis[-1], freqs[sel][0], freqs[sel][-1]],
                  vmin=0, vmax=1, interpolation="nearest")
        ax.set_yscale("log" if lo <= 0 else "linear")
        ax.set_ylabel("Hz (log)" if lo <= 0 else "Hz")
        ax.set_xlabel("time (s)")
        ax.set_title(f"{label}   [{n_bins} bins]")
        ax.grid(alpha=0.25, color="w", lw=0.4)
        for ot in onset_t:
            ax.axvline(ot, color="w", alpha=0.22, lw=0.5)

    ax = fig.add_subplot(gs[0, :])
    s = spec / (spec.max() + 1e-9)
    s = np.power(s, 0.45)
    ax.imshow(s, aspect="auto", origin="lower", cmap="magma",
              extent=[t_axis[0], t_axis[-1], freqs[0], freqs[-1]],
              vmin=0, vmax=1, interpolation="nearest")
    ax.set_yscale("log")
    ax.set_ylabel("Hz (log)")
    ax.set_xlabel("time (s)")
    ax.set_title(f"{title}   FULL SPECTRUM (log freq)   |   青线=鼓点(onset)")
    ax.grid(alpha=0.25, color="w", lw=0.4)
    for ot in onset_t:
        ax.axvline(ot, color="cyan", alpha=0.4, lw=0.7)

    band(fig.add_subplot(gs[1, 0]), 20, 200, "LOW 20-200Hz  (kick/bass 落地感)", "inferno")
    band(fig.add_subplot(gs[1, 1]), 200, 2000, "MID 200-2kHz  (melody/vocal)", "viridis")
    band(fig.add_subplot(gs[2, 0]), 2000, 16000, "HIGH 2k-16kHz  (air/cyan 镲片)", "cool")

    ax = fig.add_subplot(gs[2, 1])
    # ⚠ 包络是 50ms 步长, onset 是 STFT hop(23ms) → 两者长度不同,
    #   画在同一张图上必须各自用自己的时间轴, 不能共用 t_axis。
    t_env = np.arange(len(dbn)) * (w_i / SR)
    ax.plot(t_env, dbn, color="#00e5ff", lw=1.0)
    ax.fill_between(t_env, dbn, alpha=0.18, color="#00e5ff")
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("normalized energy")
    ax.set_title("ENERGY ENVELOPE  (结构/爬升)")
    ax.grid(alpha=0.3)
    seg = np.array_split(dbn, 4)
    bounds = [0, len(dbn) // 4, len(dbn) // 2, 3 * len(dbn) // 4, len(dbn)]
    for i in range(4):
        a, b = bounds[i], bounds[i + 1]
        m = float(seg[i].mean())
        ax.axvspan(t_env[a], t_env[min(b, len(t_env) - 1)],
                   color=f"C{i}", alpha=0.08)
        ax.text((t_env[a] + t_env[min(b, len(t_env) - 1)]) / 2,
                0.95, f"{i+1}: {m:.2f}", ha="center", fontsize=9, color=f"C{i}")
    for ot in onset_t:
        ax.axvline(ot, color="red", alpha=0.35, lw=0.5)

    fig.suptitle(title, fontsize=15, fontweight="bold")
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    return out_png


if __name__ == "__main__":
    mp3 = sys.argv[1]
    out = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else pathlib.Path(mp3).stem
    start = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
    build(mp3, out, start=start, title=title)
    print(f"✅ {out}")
