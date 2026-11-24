#!/usr/bin/env python3
"""成片音频体检 —— 一次跑完所有「口播清不清楚 / 音乐配得对不对」的客观指标。

用法:
    python scripts/audio_audit.py <成片.mp4> <纯音乐轨.wav> <纯口播.mp4>

三个输入:
    成片.mp4        交付物本身（不是中间产物）
    纯音乐轨.wav    口播权重置 0 导出的音乐轨（mix 脚本要一起导）
    纯口播.mp4      只有旁白、没有音乐的底版（用来做 whisper 地面真值）

输出六项:
    ① 首秒包络     —— t=0 是否已是高位（"一打开就是高潮"）
    ② digital click —— 满电平硬切的爆音检测
    ③ 旁白/音乐带内比 —— 真正的掩蔽指标（见 pitfalls G41）
    ④ whisper 拼音一致度 —— 地面真值（见 pitfalls G43）
    ⑤ 音乐层次     —— intro/outro 比 body 高多少 dB
    ⑥ 帧间隔完整性 —— 全部恒为 1/fps

依赖: numpy + torch + transformers (AST) + faster_whisper + pypinyin
⚠ 这些重依赖常装在某个特定解释器/venv 里, 不一定是默认 python3。
   用 $TUTORIAL_PYTHON 指定, 或让脚本自己探测。
"""
import difflib
import os
import subprocess
import sys
import tempfile
import unicodedata
import wave

import numpy as np

SR = 16000
VOICE_LO, VOICE_HI = 300, 3400


def grab(path, t, dur, out, sr=SR):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}",
                    "-t", f"{dur:.3f}", "-i", path, "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", out], check=True)
    w = wave.open(out, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    return x


def band(x, lo=VOICE_LO, hi=VOICE_HI):
    if len(x) < 64:
        return 1e-12
    N = len(x)
    P = (np.abs(np.fft.rfft(x)) ** 2) / N
    fr = np.fft.rfftfreq(N, 1.0 / SR)
    return P[(fr >= lo) & (fr < hi)].sum() + 1e-12


def dbfs(x):
    return 20 * np.log10(float(np.sqrt((x ** 2).mean())) + 1e-9)


def pinyin_seq(s):
    """中文转拼音(无声调) —— 免疫繁简/同音错字/标点。"""
    from pypinyin import lazy_pinyin, Style
    s = unicodedata.normalize("NFKC", s)
    out = []
    for p in lazy_pinyin(s, style=Style.NORMAL, errors=lambda x: x.lower()):
        p = "".join(c for c in p if c.isalnum())
        if p:
            out.append(p)
    return out


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    mix_path, music_path, voice_path = sys.argv[1:4]
    BG_WEIGHT = 0.34          # mix 脚本里的 amix 权重，须与实际一致
    tmp = tempfile.mkdtemp(prefix="audio_audit_")
    fails = []

    # ── ① 首秒包络 + ② click ──
    print("=" * 68)
    print("① 首秒包络（判定：第 0 帧是否已是高位 / 有无蓄势淡入）")
    print("=" * 68)
    head = grab(mix_path, 0.0, 3.0, f"{tmp}/head.wav", sr=44100)
    step = 44100 // 10
    rms_list = []
    for i in range(0, len(head) - step, step):
        rms_list.append(dbfs(head[i:i + step]))
    first1 = float(np.mean(rms_list[:10]))
    rest = float(np.mean(rms_list[10:]))
    print(f"  前 1 秒均值 = {first1:.2f} dBFS")
    print(f"  1~3 秒均值 = {rest:.2f} dBFS")
    print(f"  差值       = {first1 - rest:+.2f} dB")
    if first1 - rest < -3:
        print("  ❌ 有静默蓄势/淡入 —— 不是「一打开就是高潮」")
        fails.append("首秒有蓄势")
    else:
        print("  ✅ 第 0 帧即高位")

    print()
    print("=" * 68)
    print("② digital click（真 click = 孤立脉冲：跳变后回到原位）")
    print("=" * 68)
    d = np.diff(head[:44100])
    # 孤立脉冲: |d[i]| 与 |d[i+1]| 都大, 且 x[i+2] ≈ x[i]
    cand = np.where((np.abs(d[:-1]) > 0.03) & (np.abs(d[1:]) > 0.03) &
                     (np.abs(head[2:44100] - head[:44100 - 2]) < 0.01))[0]
    print(f"  孤立脉冲数 = {len(cand)}")
    if len(cand):
        print(f"  ❌ 有 digital click（首个在 {cand[0]/44100*1000:.1f}ms）")
        fails.append("digital click")
    else:
        print("  ✅ 无 click")

    # ── ③ 旁白/音乐带内比 ──
    print()
    print("=" * 68)
    print("③ 旁白/音乐 带内比（成片带内 − 音乐贡献；判据 ≥ +20dB）")
    print("=" * 68)
    WINDOWS = [(0.0, 4.0, "intro 开头"), (4.0, 4.0, "intro 4-8s"),
               (10.0, 4.0, "intro 10-14s"), (20.0, 4.0, "intro 20-24s"),
               (60.0, 4.0, "body 60-64s"), (150.0, 4.0, "body 150-154s"),
               (235.0, 4.0, "outro 235-239s"), (245.0, 4.0, "outro 245-249s")]
    snrs = []
    for t, dur, tag in WINDOWS:
        a = grab(mix_path, t, dur, f"{tmp}/a.wav")
        m = grab(music_path, t, dur, f"{tmp}/m.wav")
        mus = band(m) * (BG_WEIGHT ** 2)
        voice = max(band(a) - mus, 1e-12)
        snr = 10 * np.log10(voice / max(mus, 1e-12))
        snrs.append((snr, tag))
        flag = "✅" if snr >= 20 else "⚠️"
        print(f"  {flag} {tag:<16} 旁白/音乐 = {snr:+7.2f} dB")
    lo_snr = min(s for s, _ in snrs)
    if lo_snr < 15:
        fails.append(f"旁白/音乐最低仅 {lo_snr:+.1f}dB")

    # ── ④ whisper 地面真值 ──
    print()
    print("=" * 68)
    print("④ whisper 拼音一致度（地面真值；判据 ≥0.85）")
    print("=" * 68)
    try:
        from faster_whisper import WhisperModel
        wm = WhisperModel("small", device="cpu", compute_type="int8")
        ratios = []
        for t, dur, tag in WINDOWS:
            grab(mix_path, t, dur, f"{tmp}/w1.wav")
            grab(voice_path, t, dur, f"{tmp}/w2.wav")
            s1, _ = wm.transcribe(f"{tmp}/w1.wav", language="zh", beam_size=5)
            s2, _ = wm.transcribe(f"{tmp}/w2.wav", language="zh", beam_size=5)
            txt1 = "".join(s.text for s in s1)
            txt2 = "".join(s.text for s in s2)
            r = difflib.SequenceMatcher(None, pinyin_seq(txt1), pinyin_seq(txt2)).ratio()
            ratios.append((r, tag))
            flag = "✅" if r >= 0.85 else ("⚠️" if r >= 0.7 else "❌")
            print(f"  {flag} {tag:<16} 拼音ratio = {r:.3f}")
            if r < 0.9:
                print(f"      成片: {txt1.strip()}")
                print(f"      干声: {txt2.strip()}")
        lo_r = min(r for r, _ in ratios)
        if lo_r < 0.85:
            fails.append(f"whisper 最低一致度 {lo_r:.3f}")
    except ImportError as e:
        print(f"  ⚠️ 跳过（{e}）—— 需 faster_whisper + pypinyin")
        print("     Python: 用装了 numpy/faster_whisper 的那个 venv")

    # ── ⑤ 音乐层次 ──
    print()
    print("=" * 68)
    print("⑤ 音乐层次（intro/outro 应比 body 高 ≥10dB）")
    print("=" * 68)
    lv = {}
    for tag, t0, t1 in [("intro", 0.0, 24.36), ("body", 24.36, 230.72),
                        ("outro", 230.72, 253.32)]:
        x = grab(music_path, t0, t1 - t0, f"{tmp}/l.wav")
        lv[tag] = dbfs(x)
        print(f"  {tag:<6} 音乐 RMS {lv[tag]:7.2f} dBFS")
    print(f"  → intro 比 body 高 {lv['intro'] - lv['body']:+.2f} dB")
    print(f"  → outro 比 body 高 {lv['outro'] - lv['body']:+.2f} dB")
    if lv["intro"] - lv["body"] < 10 or lv["outro"] - lv["body"] < 10:
        fails.append("音乐层次不足 10dB")

    # ── ⑥ 帧间隔 ──
    print()
    print("=" * 68)
    print("⑥ 帧间隔完整性（期望全部 == 1/fps）")
    print("=" * 68)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", mix_path],
        capture_output=True, text=True).stdout
    pts = []
    for line in out.split("\n"):
        try:
            pts.append(float(line.strip().split(",")[0]))
        except (ValueError, IndexError):
            pass
    pts.sort()   # ⚠ B 帧重排 → 必须先排序
    from collections import Counter
    diffs = Counter(round(pts[i + 1] - pts[i], 6) for i in range(len(pts) - 1))
    print(f"  帧数 = {len(pts)}")
    for d, n in diffs.most_common():
        flag = "✅" if abs(d - 0.04) < 1e-6 else "❌"
        print(f"    {d:.6f}s × {n:<6} {flag}")
    bad = sum(n for d, n in diffs.items() if abs(d - 0.04) > 1e-6)
    if bad:
        fails.append(f"{bad} 个异常帧间隔")

    print()
    print("=" * 68)
    if fails:
        print(f"❌ 未通过：{'; '.join(fails)}")
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
