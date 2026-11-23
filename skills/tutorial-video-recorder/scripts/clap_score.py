#!/usr/bin/env python3
"""CLAP 零样本音频-文本匹配 —— 直接问模型"这首曲子像不像带劲的科技配乐"。

用 LAION CLAP (htsat-unfused), CPU 可跑。给多组正/负提示, 取平均相似度差。

用法: clap_score.py <mp3> [mp3 ...]
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hf_setup  # noqa: E402

hf_setup.configure()   # 必须在 import transformers 之前

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import torch

MODEL = "laion/clap-htsat-unfused"
SR = 48000

# 正向: 用户要的"激情配乐"
POS = [
    "energetic electronic music with a driving beat and a build up to a drop",
    "high energy techno track with punchy kick drum",
    "epic powerful motivational electronic background music for a tech video",
    "fast paced drum and bass with heavy bass and crisp hi hats",
    "uplifting corporate electronic music that feels exciting and modern",
]
# 负向: 用户骂的"垃圾配乐"
NEG = [
    "boring slow corporate background music, generic stock library loop",
    "cheesy inspirational corporate motivational jingle, soft and harmless",
    "sad melancholy ambient pad with no rhythm",
    "plain minimal bland background music that feels like office lobby music",
    "weak muffled lo-fi tune with no energy and no drums",
]


def load_audio(path, seconds=30.0, start=20.0):
    import subprocess
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds),
         "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if len(x) < SR:
        return None
    return x


def main(paths):
    from transformers import ClapModel, ClapProcessor
    proc = ClapProcessor.from_pretrained(MODEL)
    model = ClapModel.from_pretrained(MODEL).eval()

    def feat(x):
        # transformers 5.x: get_*_features 可能返回 ModelOutput 而非 Tensor
        if not isinstance(x, torch.Tensor):
            x = getattr(x, "pooler_output", None)
            if x is None:
                x = x[0]
        return x

    with torch.no_grad():
        tin = proc(text=POS + NEG, return_tensors="pt", padding=True)
        tf = feat(model.get_text_features(**tin))
        tf = tf / tf.norm(dim=-1, keepdim=True)

    for p in paths:
        x = load_audio(p)
        if x is None:
            print(f"{p}\tTOO_SHORT")
            continue
        with torch.no_grad():
            ai = proc(audio=x, sampling_rate=SR, return_tensors="pt")
            af = feat(model.get_audio_features(**ai))
            af = af / af.norm(dim=-1, keepdim=True)
            logits = af @ tf.T
        pos = float(logits[0, :len(POS)].mean())
        neg = float(logits[0, len(POS):].mean())
        print(f"{os.path.basename(p)}\tPOS={pos:.4f}\tNEG={neg:.4f}\t"
              f"MARGIN={pos - neg:+.4f}\tGUN={float(logits[0].mean()):.4f}")


if __name__ == "__main__":
    main(sys.argv[1:])
