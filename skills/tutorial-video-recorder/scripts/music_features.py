#!/usr/bin/env python3
"""音乐可听要素分析 —— 判断"是不是激情配乐"的客观依据。

🚨 为什么不能用频谱图打分:
    实测: 高端 Corporate 配乐和真正带劲的电子乐, 频谱长得几乎一样
    (都是"高频延展 + 低频厚 + 有起伏")。视觉模型给两者都高分。
    → 用户听完直接骂"垃圾配乐"。
    频谱图只能回答"有没有能量", 回答不了"是什么音乐"。

🚨🚨🚨 **STFT 窗口长度必须 >> hop** (这是低频分析的命门):
    第一版用 hop=10ms 同时当 FFT 长度 → 频率分辨率 = SR/220 = **100Hz/bin**
    → 20-200Hz 只有 **1 个 bin** → 低频完全测不准:
       · 视觉图的 LOW 面板渲染成空(子代理识破了这是假象)
       · kick(低频冲击) 指标全为 0
    正确做法: **窗长 2048~4096, hop 512**(Winograd 式重叠):
       2048 点 @22050 → 分辨率 **10.8Hz**, 20-200Hz 有 17 个 bin ✓

✅ 这套指标抓的是**可听的**东西:
    1. 鼓点密度(onset/BPM)      —— 有没有推动力
    2. 瞬态锐度(onset attack)    —— 鼓点是不是"啪"地砸下去(而不是软的)
    3. 低频冲击(kick energy)     —— 是否有"落地感" ← 区分 corporate 的关键
    4. 动态范围(crest/dyn)       —— 是否有强弱对比(静→爆)
    5. 高频延展(brilliance)      —— 镲片/合成器是否明亮
    6. 段落结构(build-up)        —— 有没有"越来越强"的能量爬升
    7. 和声丰富度(chroma)        —— 音乐是否有色彩(单音铺底 vs 丰富和弦)
    8. 响度一致性                —— 是否忽大忽小
"""
import concurrent.futures as cf
import json
import pathlib
import subprocess
import sys

import numpy as np

SR = 22050
NFFT = 2048           # FFT 长度 → 频率分辨率 10.77Hz
HOP = 512             # 跳进 23.2ms (重叠 75%)
WIN = int(SR * 0.05)  # RMS 分析窗 50ms


def load(mp3, max_sec=90, start=0.0):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(max_sec),
         "-i", str(mp3), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def stft(x):
    """长窗 STFT → (spec[freq,time], freqs)"""
    n_frames = 1 + (len(x) - NFFT) // HOP
    if n_frames < 10:
        return None, None
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = x[idx] * np.hanning(NFFT)[None, :]
    spec = np.abs(np.fft.rfft(frames, axis=1)).T
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    return spec, freqs


def analyze(path, start=0.0, max_sec=90):
    x = load(path, max_sec, start)
    if len(x) < SR:
        return None
    spec, freqs = stft(x)
    if spec is None:
        return None
    hop_s = HOP / SR
    t_axis = np.arange(spec.shape[1]) * hop_s

    # ── RMS 包络 (50ms 窗) ──
    w_i = WIN
    n = (len(x) - w_i) // w_i
    rms = np.sqrt((x[:n * w_i].reshape(n, w_i) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    dbn = (db - db.min()) / (db.max() - db.min() + 1e-9)

    # ── 谱通量(onset) ──
    flux = np.maximum(0.0, np.diff(spec, axis=1)).sum(0)
    if flux.max() > 0:
        flux = flux / flux.max()
    thr = np.percentile(flux, 92)
    peaks = [i for i in range(2, len(flux) - 2)
             if flux[i] > thr and flux[i] == max(flux[i - 2:i + 3])]
    pruned = []
    for p in peaks:
        if not pruned or (p - pruned[-1]) * hop_s > 0.05:
            pruned.append(p)
    dur = len(x) / SR
    onsets_per_sec = len(pruned) / dur
    bpm = onsets_per_sec * 60 / 2 if onsets_per_sec else 0

    # ── 瞬态锐度 ──
    rise = [flux[p] - flux[p - 3] for p in pruned if p > 3]
    sharpness = float(np.mean(rise)) if rise else 0.0

    # ── 低频冲击 (20-200Hz, 现在有 17 个 bin) ──
    lo_bins = int(20 / (SR / NFFT))
    hi_bins = int(200 / (SR / NFFT))
    lowband = spec[lo_bins:hi_bins].sum(0)
    lowband = lowband / (lowband.max() + 1e-9)
    kick = float(np.mean([lowband[p] for p in pruned])) if pruned else 0.0
    # 低频持续性: 低频带能量的变异系数(越小=越持续稳)
    low_rms = float(lowband.std() / (lowband.mean() + 1e-9))

    # ── 动态 ──
    dyn = float(np.percentile(dbn, 95) - np.percentile(dbn, 5))
    crest = float(np.max(rms) / (np.sqrt((rms ** 2).mean()) + 1e-12))

    # ── 高频延展 (6k-16k) ──
    f = freqs
    hi = spec[(f > 6000)].sum(0)
    mid = spec[(f > 250) & (f <= 6000)].sum(0)
    brilliance = float(hi.sum() / (mid.sum() + 1e-9))

    # ── 段落结构 ──
    seg = np.array_split(dbn, 4)
    seg_means = [float(s.mean()) for s in seg]
    build = seg_means[-1] - seg_means[0]

    # ── 和声丰富度 (chroma 熵) ──
    valid = f > 60
    midi = np.round(69 + 12 * np.log2(np.maximum(f, 1e-6) / 440.0)).astype(int)
    chroma = np.array([spec[valid & (midi % 12 == i)].sum() for i in range(12)])
    p = chroma / (chroma.sum() + 1e-9)
    nz = p[p > 0.02]
    entropy = float(-(nz * np.log2(nz)).sum() / np.log2(12)) if len(nz) > 1 else 0.0

    # ── 响度一致性 ──
    sec_n = int(1.0 / (w_i / SR))
    secs = [float(dbn[i:i + sec_n].mean()) for i in range(0, len(dbn) - sec_n, sec_n)]
    consist = float(np.std(secs)) if secs else 0.0

    def norm(v, lo, hi):
        return float(np.clip((v - lo) / (hi - lo + 1e-9), 0, 1))

    s_onset = norm(onsets_per_sec, 1.0, 6.0)
    s_sharp = norm(sharpness, 0.005, 0.05)
    s_kick = norm(kick, 0.15, 0.60)
    s_kicksus = norm(1.0 - low_rms, 0.30, 0.95)   # 低频越持续越好
    s_dyn = norm(dyn, 0.15, 0.60)
    s_bril = norm(brilliance, 0.02, 0.35)
    s_build = norm(build, -0.15, 0.25)
    s_harm = norm(entropy, 0.35, 0.85)
    s_cons = float(np.clip(1.0 - consist / 0.35, 0, 1))

    score = (s_onset * 0.13 + s_sharp * 0.11 + s_kick * 0.16 + s_kicksus * 0.08
             + s_dyn * 0.14 + s_bril * 0.12 + s_build * 0.14 + s_harm * 0.07
             + s_cons * 0.05)
    return {
        "file": pathlib.Path(path).name,
        "dur": round(dur, 1),
        "onsets_per_sec": round(onsets_per_sec, 2),
        "bpm_est": round(bpm, 1),
        "sharpness": round(sharpness, 4),
        "kick": round(kick, 3),
        "kick_sustain": round(low_rms, 3),
        "dyn": round(dyn, 3),
        "crest": round(crest, 2),
        "brilliance": round(brilliance, 3),
        "build": round(build, 3),
        "harmony": round(entropy, 3),
        "consistency": round(consist, 3),
        "seg_energy": [round(x, 2) for x in seg_means],
        "scores": {"onset": round(s_onset, 2), "sharp": round(s_sharp, 2),
                   "kick": round(s_kick, 2), "kicksus": round(s_kicksus, 2),
                   "dyn": round(s_dyn, 2), "bril": round(s_bril, 2),
                   "build": round(s_build, 2), "harm": round(s_harm, 2),
                   "cons": round(s_cons, 2)},
        "energy_score": round(score * 100, 1),
    }


if __name__ == "__main__":
    files = sys.argv[1:]
    if not files:
        print("用法: music_features.py <mp3...>")
        raise SystemExit(1)
    with cf.ThreadPoolExecutor(6) as ex:
        out = [r for r in ex.map(lambda f: analyze(f), files) if r]
    out.sort(key=lambda x: -x["energy_score"])
    print(f"{'file':>12} {'ENERGY':>6} {'ons/s':>6} {'sharp':>6} {'KICK':>6} "
          f"{'kicksus':>7} {'dyn':>5} {'bril':>5} {'build':>6} {'harm':>5}")
    for r in out:
        print(f"{r['file']:>12} {r['energy_score']:>6} {r['onsets_per_sec']:>6} "
              f"{r['sharpness']:>6} {r['kick']:>6} {r['kick_sustain']:>7} "
              f"{r['dyn']:>5} {r['brilliance']:>5} {r['build']:>6} {r['harmony']:>5}")
    json.dump(out, open("/tmp/music_features.json", "w"), indent=1)
    print("\n→ /tmp/music_features.json")
