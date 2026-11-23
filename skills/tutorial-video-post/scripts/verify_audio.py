#!/usr/bin/env python3
"""成片音频验收 —— 四项硬指标, 全部客观可复现。

  ① 旁白清晰度(whisper 地面真值)
     转写**成片** vs 转写**干声**, 用**拼音序列**比相似度。
     🚨 别用繁简映射表(不完整就崩) / 别用字符 Jaccard(标点繁简都扣分)。
     🚨 窗口要 ≥4s 且 beam_size=5: 3s+beam1 会让同一段转写随机抖动,
        拿抖动当音频问题会误判。
  ② 旁白/音乐 带内比(300–3400Hz)
     🚨 必须**扣除音乐贡献**: 用 musiconly 轨反推。
        直接拿成片比干声 → 漏掉 loudnorm 增益 → 算出负 SNR 的假警报。
     判据: ≥ +20dB = 旁白清晰。
  ③ 冲击包络(段首是否满能量 + 有无 digital click)
  ④ 帧完整性(所有 pts 间隔恒为 1/fps, 无空洞/半帧)

用法:
    python verify_audio.py final.mp4 --silent silent.mp4 --timeline timeline.json
    python verify_audio.py final.mp4 --quick
"""
import argparse
import difflib
import json
import pathlib
import subprocess
import sys
import unicodedata
import wave

import numpy as np

SR = 16000


def grab(path, t, dur, out, sr=SR):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}",
                    "-t", f"{dur:.3f}", "-i", str(path), "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", out], check=True)
    w = wave.open(out, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    return x


def band(x, lo=300, hi=3400):
    if len(x) < 64:
        return 1e-12
    N = len(x)
    X = np.fft.rfft(x)
    P = (np.abs(X) ** 2) / N
    fr = np.fft.rfftfreq(N, 1 / SR)
    return P[(fr >= lo) & (fr < hi)].sum() + 1e-12


def pinyin_seq(s):
    """中文转拼音(无声调) —— 免疫繁简/同音错字/标点差异。"""
    try:
        from pypinyin import lazy_pinyin, Style
    except ImportError:
        # 没装 pypinyin → 退化成 NFKC + 去标点(繁简仍会扣分)
        s = unicodedata.normalize("NFKC", s)
        return "".join(c for c in s if "一" <= c <= "鿿" or c.isalnum())
    s = unicodedata.normalize("NFKC", s)
    out = []
    for p in lazy_pinyin(s, style=Style.NORMAL, errors=lambda x: x.lower()):
        p = "".join(c for c in p if c.isalnum())
        if p:
            out.append(p)
    return out


def check_whisper(mix, silent, points, min_ratio=0.85):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  ⚠️ 没装 faster_whisper, 跳过(用 hermes 自带 venv 的 python)")
        return None
    import warnings
    warnings.filterwarnings("ignore")
    wm = WhisperModel("small", device="cpu", compute_type="int8")
    rows = []
    for t, dur, tag in points:
        grab(mix, t, dur, "/tmp/_va_w1.wav")
        grab(silent, t, dur, "/tmp/_va_w2.wav")
        s1, _ = wm.transcribe("/tmp/_va_w1.wav", language="zh", beam_size=5)
        s2, _ = wm.transcribe("/tmp/_va_w2.wav", language="zh", beam_size=5)
        t1 = "".join(s.text for s in s1)
        t2 = "".join(s.text for s in s2)
        ratio = difflib.SequenceMatcher(None, pinyin_seq(t1), pinyin_seq(t2)).ratio()
        rows.append((ratio, tag, t1, t2))
        flag = "✅" if ratio >= min_ratio else ("⚠️" if ratio >= 0.7 else "❌")
        print(f"  {flag} {tag:<18} 拼音ratio={ratio:.3f}")
        if ratio < 0.9:
            print(f"      成片: {t1.strip()}")
            print(f"      干声: {t2.strip()}")
    vals = [r[0] for r in rows]
    print(f"  → 最低 {min(vals):.3f} 平均 {np.mean(vals):.3f} (判据 ≥{min_ratio})")
    return rows


def check_snr(mix, musiconly, points, bg_weight=0.34, min_db=20.0):
    print("\n=== ② 旁白/音乐 带内比(已扣除音乐贡献) ===")
    vals = []
    for t, dur, tag in points:
        a = grab(mix, t, dur, "/tmp/_va_s1.wav")
        m = grab(musiconly, t, dur, "/tmp/_va_s2.wav")
        mus = band(m) * (bg_weight ** 2)
        voice = max(band(a) - mus, 1e-12)
        db = 10 * np.log10(voice / max(mus, 1e-12))
        vals.append(db)
        flag = "✅" if db >= min_db else "⚠️"
        print(f"  {flag} {tag:<18} {db:+7.2f} dB")
    print(f"  → 最低 {min(vals):+.2f} 平均 {np.mean(vals):+.2f} dB (判据 ≥{min_db})")
    return vals


def check_impact(musiconly, points):
    print("\n=== ③ 段首冲击包络 + digital click ===")
    for t, dur, tag in points:
        x = grab(musiconly, t, dur, "/tmp/_va_i1.wav", sr=44100)
        step = 44100 // 20
        env = []
        for i in range(0, min(len(x), 44100), step):
            seg = x[i:i + step]
            if len(seg) < 100:
                break
            env.append(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9))
        # digital click: 孤立脉冲(跳变后**回到原位**)
        # 🚨 判据要加"能量足够高"的前提 —— 在 -35dB 的安静段里,
        #    源音乐自带的采样毛刺(hi-hat 数字尖刺)也会命中 0.03 阈值,
        #    实测选段版误报 3 处(后/前能量比仅 ±0.6dB = 能量没变 → 是源噪声不是爆音)。
        #    真 click 的特征: 跳变发生在**有声处**且跳变后能量明显下降。
        d = np.diff(x[:44100])
        clicks = 0
        if len(d) > 3:
            thr = 0.03
            for i in np.where((np.abs(d[:-1]) > thr) & (np.abs(d[1:]) > thr) &
                              (np.abs(x[2:] - x[:-2]) < 0.01))[0]:
                W = 220                                   # 5ms @44.1k
                pre = x[max(0, i - W):i]
                post = x[i:i + W]
                if len(pre) < 50 or len(post) < 50:
                    continue
                pre_e = float(np.sqrt((pre ** 2).mean()) + 1e-9)
                post_e = float(np.sqrt((post ** 2).mean()) + 1e-9)
                # 必须是"有声处"(高于噪声底 20dB) 且跳变后能量明显下降
                if pre_e > 1e-3 and post_e < pre_e * 0.7:
                    clicks += 1
        head = env[0] if env else -99.0
        rest = np.mean(env[1:]) if len(env) > 1 else -99.0
        print(f"  {tag}: 首50ms={head:7.2f} dBFS  后续={rest:7.2f}  "
              f"差={head - rest:+6.2f} dB  click={clicks}")
        print("    " + " ".join(f"{v:.0f}" for v in env[:12]))


def check_frames(video, fps=25.0):
    print("\n=== ④ 帧完整性 ===")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", video],
        capture_output=True, text=True).stdout.strip().split("\n")
    pts = []
    for line in out:
        try:
            pts.append(float(line.split(",")[0]))
        except (ValueError, IndexError):
            pass
    pts.sort()
    exp = 1.0 / fps
    from collections import Counter
    c = Counter(round(pts[i + 1] - pts[i], 6) for i in range(len(pts) - 1))
    bad = sum(n for d, n in c.items() if abs(d - exp) > 1e-6)
    print(f"  帧数={len(pts)}  首pts={pts[0]:.6f}  末pts={pts[-1]:.6f}")
    for d, n in sorted(c.items()):
        print(f"    间隔 {d:.6f}s × {n}")
    print(f"  {'✅' if bad == 0 else '❌'} 异常间隔 {bad} 个 (期望恒为 {exp:.6f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--silent", default="", help="干声/无声底版(提供则跑 whisper)")
    ap.add_argument("--musiconly", default="", help="纯音乐轨(提供则跑 SNR)")
    ap.add_argument("--timeline", default="")
    ap.add_argument("--bg-weight", type=float, default=0.34)
    ap.add_argument("--quick", action="store_true", help="只跑帧检查")
    ap.add_argument("--min-ratio", type=float, default=0.85)
    ap.add_argument("--min-db", type=float, default=20.0)
    args = ap.parse_args()

    print(f"=== 验收 {pathlib.Path(args.video).name} ===")
    if not args.quick:
        points = [(0.0, 4.0, "片头 0-4s"), (4.0, 4.0, "片头 4-8s"),
                  (10.0, 4.0, "片头 10-14s"), (20.0, 4.0, "片头 20-24s")]
        if args.timeline:
            TL = json.loads(pathlib.Path(args.timeline).read_text())
            segs = [t for t in TL["tracks"] if t["kind"] == "segment"]
            outrs = [t for t in TL["tracks"] if t["kind"] == "outro"]
            if segs:
                mid = segs[len(segs) // 2]
                points.append((mid["start"], 4.0, f"正文 {mid['start']:.0f}s"))
            if outrs:
                points.append((outrs[0]["start"], 4.0, f"片尾 {outrs[0]['start']:.0f}s"))

        if args.silent:
            print("\n=== ① whisper 旁白清晰度(拼音序列) ===")
            check_whisper(args.video, args.silent, points, args.min_ratio)
        if args.musiconly:
            check_snr(args.video, args.musiconly, points, args.bg_weight, args.min_db)
            check_impact(args.musiconly, [(0.0, 1.0, "片头冲击")])
    check_frames(args.video)


if __name__ == "__main__":
    main()
