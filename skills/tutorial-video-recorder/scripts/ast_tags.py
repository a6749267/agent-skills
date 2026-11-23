#!/usr/bin/env python3
"""AST (AudioSet) 音乐分类 —— 真·听音乐出曲风标签。

AudioSet 527 类里含: Techno / Drum and bass / House music / Trance music /
Dubstep / Hip hop music / Rock music / Classical music / Corporate-ish
"Music" / "Speech" / "Silence" 等 → 能区分"带劲电子"和"corporate 励志"。

用法: ast_tags.py <mp3> [mp3 ...]
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
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000


def load_audio(path, seconds=30.0, start=20.0):
    import subprocess
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds),
         "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    return x


def main(paths):
    fe = AutoFeatureExtractor.from_pretrained(MODEL)
    model = AutoModelForAudioClassification.from_pretrained(MODEL).eval()

    for p in paths:
        x = load_audio(p)
        if len(x) < SR:
            print(f"{p}\tTOO_SHORT")
            continue
        with torch.no_grad():
            inputs = fe(x, sampling_rate=SR, return_tensors="pt")
            logits = model(**inputs).logits
        probs = torch.sigmoid(logits)[0].numpy()
        top = np.argsort(-probs)[:8]
        labels = model.config.id2label
        s = " | ".join(f"{labels[int(i)]}:{probs[i]:.2f}" for i in top)
        # 音乐类聚合分
        music_kw = ["Music", "musical", "Techno", "Electronic", "Dance", "House",
                    "Trance", "Dubstep", "bass", "Drum", "Synthesizer", "Rock",
                    "Hip hop", "Pop", "Beat", "Tapping", "Chord", "Guitar"]
        speech_kw = ["Speech", "Narration", "Male speech", "Female speech",
                     "Conversation", "Narrator"]
        mscore = sum(probs[i] for i in range(len(probs))
                     if any(k.lower() in labels[i].lower() for k in music_kw))
        sscore = sum(probs[i] for i in range(len(probs))
                     if any(k.lower() in labels[i].lower() for k in speech_kw))
        print(f"{os.path.basename(p)}\tMUSIC={mscore:.2f}\tSPEECH={sscore:.2f}\t{s}")


if __name__ == "__main__":
    main(sys.argv[1:])
