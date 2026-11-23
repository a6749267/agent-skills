#!/usr/bin/env python3
"""音乐识别 —— 用真模型"听"曲子, 输出客观标签, 支撑选曲决策。

三个独立证据源(缺一不可):
  ① AST (AudioSet 527 类)  → 曲风标签: Techno/House/Drum machine/Bass drum
                              = 带劲; Violin/Cello/Sad/Ambient = 电影/悲伤 → 排除
  ② CLAP (零样本音频-文本) → 直接问"像不像带劲的科技配乐"
                              margin = 正向提示相似度 − 负向提示相似度
                              **margin < 0 = 比 corporate 垃圾还像垃圾**
  ③ 可听要素(鼓点密度/瞬态锐度/低频冲击/动态范围) → 区分"墙纸"与"驱动"

🚨 为什么不能用频谱图打分:
    高端 Corporate 配乐和真带劲的电子乐频谱几乎一样 → 视觉模型都给高分。
    频谱图回答不了"激不激情"。

用法:
    python music_identify.py music_pool/*.mp3 --out music_pool/analysis.json
    python music_identify.py a.mp3 --clap
"""
import argparse
import json
import os
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hf_setup  # noqa: E402

hf_setup.configure()   # 必须在 import transformers 之前

import numpy as np

SR_AST = 16000
SR_CLAP = 48000

# AST 关键词: 带劲 vs 电影感
HOT_LABELS = [
    "Techno", "House music", "Trance music", "Drum and bass", "Electronic music",
    "Dubstep", "Hip hop music", "Rock music", "Dance music", "Disco",
    "Bass drum", "Drum machine", "Drum roll", "Snare drum", "Beat",
]
COLD_LABELS = [
    "Violin", "Cello", "Piano", "Classical music", "Ambient music",
    "New-age music", "Sad music", "Choir", "Singing bowl", "Music of Latin America",
    "Slow music", "Soft music",
]

# CLAP 正/负提示
CLAP_POS = [
    "energetic electronic music with driving beat and build up",
    "powerful epic percussion hit with impact",
    "upbeat techno with strong bass drop",
    "dynamic soundtrack with sharp transients",
]
CLAP_NEG = [
    "boring generic corporate stock loop",
    "soft calm ambient pad with no rhythm",
    "sad slow cinematic violin melody",
    "flat background music without dynamics",
]

AST_MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
CLAP_MODEL = "laion/clap-htsat-unfused"


def read_audio(path, sr):
    import subprocess
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"], capture_output=True).stdout
    if not raw:
        raise ValueError("ffmpeg 解码失败")
    return np.frombuffer(raw, dtype=np.float32).copy()


def ast_analyze(path, chunk=5.0):
    import torch
    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
    ex = AutoFeatureExtractor.from_pretrained(AST_MODEL)
    model = AutoModelForAudioClassification.from_pretrained(AST_MODEL)
    x = read_audio(path, SR_AST)
    hop = int(SR_AST * chunk)
    out = []
    for i in range(0, max(1, len(x) - hop), hop):
        seg = x[i:i + hop]
        if len(seg) < SR_AST // 2:
            continue
        inp = ex(seg, sampling_rate=SR_AST, return_tensors="pt")
        with torch.no_grad():
            logits = model(**inp).logits
        p = torch.sigmoid(logits)[0].numpy()
        idx = np.argsort(-p)[:12]
        out.append({model.config.id2label[j]: float(p[j]) for j in idx})
    hot = cold = 0
    music = speech = 0.0
    top_tags = {}
    for ch in out:
        for h in HOT_LABELS:
            if h in ch:
                hot += 1
                break
        if any(c in ch for c in COLD_LABELS):
            cold += 1
        music += ch.get("Music", 0.0)
        speech += ch.get("Speech", 0.0)
        for k, v in ch.items():
            if k not in top_tags or v > top_tags[k]:
                top_tags[k] = v
    n = max(1, len(out))
    return {
        "ast_hot_ratio": round(hot / n, 3),
        "ast_cold_ratio": round(cold / n, 3),
        "ast_music": round(music / n, 3),
        "ast_speech": round(speech / n, 3),
        "ast_top": sorted(top_tags.items(), key=lambda kv: -kv[1])[:8],
    }


def clap_score(path):
    import torch
    from transformers import AutoProcessor, ClapModel
    proc = AutoProcessor.from_pretrained(CLAP_MODEL)
    model = ClapModel.from_pretrained(CLAP_MODEL)
    x = read_audio(path, SR_CLAP)
    if len(x) < SR_CLAP:
        x = np.pad(x, (0, SR_CLAP - len(x)))
    x = x[:SR_CLAP * 180]           # 最多 3 分钟
    # 🚨 transformers 新版把 `audios=` 改名成 `audio=` → 兼容两种写法
    inp = proc(audio=x, sampling_rate=SR_CLAP, return_tensors="pt")
    with torch.no_grad():
        ae = model.get_audio_features(**inp)
    # 🚨 新版 get_audio_features 返回 **ModelOutput** 而不是 Tensor
    #    → 要取 pooler_output, 否则报 "'BaseModelOutputWithPooling' object has no attribute 'norm'"
    if not isinstance(ae, torch.Tensor):
        ae = getattr(ae, "pooler_output", None)
        if ae is None:
            ae = ae[0]
    ae = ae / ae.norm(dim=-1, keepdim=True)
    sims = []
    for texts in (CLAP_POS, CLAP_NEG):
        ti = proc(text=texts, padding=True, return_tensors="pt")
        with torch.no_grad():
            te = model.get_text_features(**ti)
        if not isinstance(te, torch.Tensor):
            te = getattr(te, "pooler_output", None)
            if te is None:
                te = te[0]
        te = te / te.norm(dim=-1, keepdim=True)
        sims.append(float((ae @ te.T).mean()))
    pos, neg = sims
    return {
        "clap_pos": round(pos, 4),
        "clap_neg": round(neg, 4),
        "clap_margin": round(pos - neg, 4),
    }


def audible_features(path):
    """可听要素: 鼓点密度 / 瞬态锐度 / 低频冲击 / 动态范围。
    🚨 STFT 窗长必须 >> hop(低频分析的命门):
        用 hop=10ms 同时当 FFT 长度 → 分辨率 100Hz/bin → 20-200Hz 只有 1 个 bin
        → kick 能量全为 0。正确: 窗长 2048~4096, hop 512。
    """
    import subprocess
    SR = 22050
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).copy()
    if len(x) < SR:
        return {}
    N, HOP = 2048, 512
    win = np.hanning(N)
    frames = []
    for i in range(0, len(x) - N, HOP):
        frames.append(np.abs(np.fft.rfft(x[i:i + N] * win)))
    if len(frames) < 4:
        return {}
    S = np.array(frames)
    flux = np.maximum(0, np.diff(S, axis=0)).sum(axis=1)
    flux = flux / (flux.max() + 1e-9)
    # onset 计数(阈值 + 局部极大)
    thr = 0.18
    onsets = 0
    for i in range(2, len(flux) - 2):
        if flux[i] > thr and flux[i] == max(flux[i - 2:i + 3]):
            onsets += 1
    dur = len(x) / SR
    onset_rate = onsets / max(dur, 1e-9)
    # 低频冲击: 30-140Hz 的谱通量占比
    freqs = np.fft.rfftfreq(N, 1 / SR)
    low = (freqs >= 30) & (freqs < 140)
    low_flux = np.maximum(0, np.diff(S[:, low], axis=0)).sum()
    tot_flux = np.maximum(0, np.diff(S, axis=0)).sum() + 1e-9
    # 动态范围: RMS 分段峰谷差
    # 🚨 不能直接用 max-min: 曲子里的静音段会把 min 拉到 -100dB → 动态范围虚高到 175dB。
    #    只统计**有声段**(RMS > 全曲峰值 - 30dB)。用 45dB 阈值实测仍虚高到 44dB。
    seg = int(SR * 0.25)
    rms = []
    for i in range(0, len(x) - seg, seg):
        s = x[i:i + seg]
        rms.append(np.sqrt((s ** 2).mean()))
    rms = np.array(rms) if rms else np.array([0.0])
    db = 20 * np.log10(rms + 1e-9)
    audible = db[db > db.max() - 30.0] if len(db) else db
    dyn = float(audible.max() - audible.min()) if len(audible) > 1 else 0.0
    return {
        "duration_s": round(dur, 2),
        "onset_rate_per_s": round(onset_rate, 3),
        "low_impact_ratio": round(float(low_flux / tot_flux), 4),
        "dynamic_range_db": round(dyn, 2),
        "rms_mean_db": round(float(db.mean()), 2),
    }


def verdict(feat):
    """给一个综合判断(仅供参考, 最终还要人耳试听)。
    🚨 分析失败 ≠ 不合格 → 缺 AST/CLAP 时只能说"未验证", 不能判 ❌。
    """
    if feat.get("ast_error") or feat.get("clap_error"):
        missing = []
        if feat.get("ast_error"):
            missing.append("AST")
        if feat.get("clap_error"):
            missing.append("CLAP")
        return f"⚠️ {'/'.join(missing)} 未跑(缺依赖), 仅可听要素可用"
    clap_m = feat.get("clap_margin")
    hot = feat.get("ast_hot_ratio", 0)
    cold = feat.get("ast_cold_ratio", 0)
    onr = feat.get("onset_rate_per_s", 0)
    reasons = []
    if clap_m is not None and clap_m <= 0:
        reasons.append("CLAP margin<=0(比 corporate 垃圾还像垃圾)")
    if cold >= 0.5:
        reasons.append("AST 冷门标签过半(电影/悲伤感)")
    if hot < 0.4:
        reasons.append("AST 冲击标签命中不足")
    if onr and onr < 0.8:
        reasons.append(f"起伏次数少({onr}/s, 墙纸音乐)")
    return "❌ " + "; ".join(reasons) if reasons else "✅ 可用"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="")
    ap.add_argument("--no-clap", action="store_true", help="跳过 CLAP(慢)")
    ap.add_argument("--no-features", action="store_true", help="跳过可听要素分析")
    args = ap.parse_args()

    results = {}
    for f in args.files:
        p = pathlib.Path(f)
        if not p.exists():
            continue
        feat = {}
        try:
            feat.update(audible_features(p) if not args.no_features else {})
        except Exception as e:
            feat["features_error"] = str(e)
        try:
            feat.update(ast_analyze(p))
        except Exception as e:
            feat["ast_error"] = str(e)
        if not args.no_clap:
            try:
                feat.update(clap_score(p))
            except Exception as e:
                feat["clap_error"] = str(e)
        feat["verdict"] = verdict(feat)
        results[str(p)] = feat
        print(f"{p.name}: {feat['verdict']}")
        print(f"   hot={feat.get('ast_hot_ratio')} cold={feat.get('ast_cold_ratio')} "
              f"clap_margin={feat.get('clap_margin')} "
              f"onsets/s={feat.get('onset_rate_per_s')} "
              f"low={feat.get('low_impact_ratio')}")

    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
