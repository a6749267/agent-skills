#!/usr/bin/env python3
"""ASR 强制对齐 —— 用真实语音时间戳驱动字幕, 而不是按字数猜。

🚨 为什么不能用「按字数比例分配时间」:
    老方案 split_sentences() 切完句后, 按 `len(s)/total_chars` 比例摊到整段。
    这是**猜的** —— TTS 的语速不均匀(停顿、语速、句长都不同),
    实测每段能偏 0.3~1.5s, 累积到片尾能偏好几秒。
    观众看到的就是「字幕比人声慢半拍」→ 极难看。

🚨 为什么不能直接用 ASR 的识别文本当字幕:
    faster-whisper 中文同音字错得很多:
        终端→中端   这一屏→这一瓶   全程→源程   合成→审查
        全废→权费   拼接→应接       树深→数身   留空→流空
        答案→打案   浮窗→服装       手数→手术   止损→直损   止盈→指营
    但它的**时间戳是准的**。

✅ 本模块的做法 = 强制对齐 (forced alignment):
    1. ASR 出逐词时间戳 → 展开成逐字时间戳
    2. difflib 把 ASR 字符序列与脚本字符序列对齐
    3. 时间取自 ASR, **文字取自脚本原文**(零错字)
    4. ASR 漏字/错字处用前后锚点插值补时间

用法:
    python asr_align.py --script script.json --audio-dir audio --out align.json
    # 然后 compose.py 用 --align align.json 走对齐轴
"""
import argparse
import difflib
import json
import pathlib
import sys

# 标点不参与对齐(ASR 完全没有标点)
PUNCT = set("，。！？；：、,.!?;:()（）「」“”\"'…—《》〈〉【】[]{}·-～~")


def asr_to_chars(words):
    """ASR 词 → [(字符, start, end)]。词内字符均分该词时长。"""
    out = []
    for w in words:
        txt = "".join(c for c in (w.get("w") or "")
                      if c not in PUNCT and not c.isspace())
        if not txt:
            continue
        n = len(txt)
        dur = max(w["e"] - w["s"], 0.001)
        for i, ch in enumerate(txt):
            out.append((ch, w["s"] + dur * i / n, w["s"] + dur * (i + 1) / n))
    return out


def script_chars(text):
    """脚本文本 → [字符] (去掉标点空白)。"""
    return [c for c in (text or "") if c not in PUNCT and not c.isspace()]


def align_one(asr_words, script_text):
    """对齐一段。返回 (chars, ratio); chars = [{c,s,e}] 时间取自 ASR。"""
    ac = asr_to_chars(asr_words)
    sc = script_chars(script_text)
    if not ac or not sc:
        return [], 0.0

    a_str = "".join(c for c, _, _ in ac)
    s_str = "".join(sc)
    sm = difflib.SequenceMatcher(None, a_str, s_str, autojunk=False)

    # 脚本字符下标 → ASR 字符下标
    smap = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                smap[j1 + k] = i1 + k
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            # 长度相同 → 位置一一对应(字错了但位置对)
            for k in range(j2 - j1):
                smap[j1 + k] = i1 + k

    times = [None] * len(sc)
    for j, i in smap.items():
        times[j] = (ac[i][1], ac[i][2])

    known = [j for j, t in enumerate(times) if t is not None]
    if not known:
        return [], 0.0
    for j in range(len(times)):
        if times[j] is not None:
            continue
        prev = max((k for k in known if k < j), default=None)
        nxt = min((k for k in known if k > j), default=None)
        if prev is not None and nxt is not None:
            t0, t1 = times[prev][1], times[nxt][0]
            span = max(nxt - prev, 1)
            frac = (j - prev) / span
            times[j] = (t0 + (t1 - t0) * frac,
                        t0 + (t1 - t0) * (frac + 1.0 / span))
        elif prev is not None:
            times[j] = (times[prev][1], times[prev][1] + 0.05)
        else:
            times[j] = (max(times[nxt][0] - 0.05, 0.0), times[nxt][0])

    chars = [{"c": sc[j], "s": round(times[j][0], 3), "e": round(times[j][1], 3)}
             for j in range(len(sc))]
    return chars, sm.ratio()


def transcribe(audio_path, model, language="zh"):
    """faster-whisper 转写 → 逐词时间戳列表。"""
    segs, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300),
        beam_size=5,
        condition_on_previous_text=False,
    )
    words = []
    for s in segs:
        for w in (s.words or []):
            txt = (w.word or "").strip()
            if not txt:
                continue
            words.append({"w": txt, "s": round(float(w.start), 3),
                          "e": round(float(w.end), 3)})
    return words, float(info.duration)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()

    script = json.loads(pathlib.Path(a.script).read_text())
    audio_dir = pathlib.Path(a.audio_dir)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("❌ 缺 faster-whisper —— pip install faster-whisper")

    model = WhisperModel(a.model, device=a.device, compute_type="int8",
                         cpu_threads=max(4, (os_cpu() or 8)))

    out = {}
    ratios = []
    for i, item in enumerate(script):
        wav = audio_dir / f"seg{i:02d}.mp3"
        if not wav.exists():
            wav = audio_dir / f"seg{i:02d}.wav"
        if not wav.exists():
            sys.exit(f"❌ 缺旁白 {wav.name}")
        words, dur = transcribe(wav, model, a.language)
        chars, r = align_one(words, item.get("text", ""))
        if not chars:
            sys.exit(f"❌ seg{i} 对齐失败(ASR 与脚本完全对不上) —— 不能静默降级")
        out[str(i)] = {"chars": chars, "ratio": round(r, 4),
                       "asr_dur": round(dur, 3)}
        ratios.append(r)
        print(f"seg{i}: {len(chars)} 字, 对齐率={r:.3f}, "
              f"跨度={chars[0]['s']:.2f}-{chars[-1]['e']:.2f}s (音频{dur:.1f}s)")

    avg = sum(ratios) / len(ratios)
    print(f"\n平均对齐率 {avg:.3f} → {a.out}")
    if avg < 0.80:
        sys.exit(f"❌ 平均对齐率 {avg:.3f} < 0.80 —— ASR 与文案严重不符, 拒绝输出")
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False))
    print(f"✅ 已写出 {a.out}")


def os_cpu():
    import os
    return os.cpu_count()


if __name__ == "__main__":
    raise SystemExit(main())
