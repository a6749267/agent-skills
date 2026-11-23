#!/usr/bin/env python3
"""BGM 选段 —— 为片头/正文/片尾各挑一个最佳取材点。

🎯 三个维度**交叉验证**(任何一个单独用都会选错):

  ① 人声带占用(300–3400Hz 功率密度 dB, 相对宽带平均)
     越**低**越好 → 音乐不会压旁白。
     🚨 千万别用"频带能量占比%" —— 宽带音乐幅度谱几乎是平的,
        占比 ≈ 带宽/奈奎斯特 = 3100/8000 = 38.75%, **跟内容无关**,
        所有候选都落在 40~52%, 毫无区分度(G37)。
        必须用**功率密度 + 等带宽归一**。

  ② 低频冲击(55–120Hz 功率密度 dB)
     越高越好 → 有"落地感", 才有"砰"。

  ③ AST 冲击类标签命中数 + Music 分
     测"这段音乐有多猛"。⚠ **AST 跑在纯音乐片段上 Speech=0.000 是正常的**
        (候选里本来没人声), 别误读成"AST 判不了"(G38)。

🎯 片头"一打开就是高潮"的额外要求(G39):
  切入点要**预滚到重拍前 0.25s**, 不是切在拍点上。
  根因: 鼓组的 kick 是**在落点才砸下的**, 落点前 200ms 是起音期
       → 切在落点 → 开头是"浮起来", 不是高潮。
  扫描所有重拍 × 多种预滚 → 预滚 0.25s 是共同最优。

用法:
    python select_segments.py bgm.mp3 --json segments.json
    python select_segments.py bgm.mp3 --intro-len 24.36 --outro-len 22.60
"""
import argparse
import json
import pathlib
import subprocess
import wave

import numpy as np

SR = 16000
BAND_VOICE = (300, 3400)
BAND_IMPACT = (55, 120)


def read_wav(path, start=0.0, dur=None):
    out = "/tmp/_sel_tmp.wav"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", out]
    subprocess.run(cmd, check=True)
    w = wave.open(out, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    return x


def band_density_db(x, lo, hi):
    """带内功率密度 相对 宽带平均功率密度 的 dB。
    <0dB = 该带比平均频段安静; >0dB = 该带突出(会压旁白)。"""
    if len(x) < 64:
        return 0.0
    N = len(x)
    X = np.fft.rfft(x)
    P = (np.abs(X) ** 2) / N
    freqs = np.fft.rfftfreq(N, 1 / SR)
    avg = P.sum() / (freqs[-1] - freqs[0]) + 1e-12
    bd = P[(freqs >= lo) & (freqs < hi)].sum() / (hi - lo)
    return float(10 * np.log10(bd / avg))


def onset_times(path, start, dur):
    """谱通量 onset 检测, 返回 (时间, 强度) 列表。"""
    SRX = 44100
    out = "/tmp/_sel_beat.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
                    "-t", f"{dur:.3f}", "-i", str(path), "-ac", "1", "-ar", str(SRX),
                    "-c:a", "pcm_s16le", out], check=True)
    w = wave.open(out, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    N, H = 2048, 256
    win = np.hanning(N)
    frames = [np.abs(np.fft.rfft(x[i:i + N] * win)) for i in range(0, len(x) - N, H)]
    if len(frames) < 4:
        return []
    S = np.array(frames)
    flux = np.maximum(0, np.diff(S, axis=0)).sum(axis=1)
    flux = flux / (flux.max() + 1e-9)
    k = 9
    fs = np.convolve(flux, np.ones(k) / k, mode="same")
    peaks = []
    for i in range(2, len(fs) - 2):
        if fs[i] == max(fs[max(0, i - 8):i + 9]) and fs[i] > 0.12:
            peaks.append((float(fs[i]), start + (i + 1) * H / SRX))
    return peaks


def head_energy(path, cut, dur=0.1):
    """切入点后前 dur 秒的 RMS(dBFS) —— 判断是否"满能量切入"。"""
    x = read_wav(path, cut, dur)
    if len(x) < 64:
        return -99.0
    return float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9))


def scan_candidates(path, intro_len, outro_len, prers, max_beats=40, scan_dur=400.0):
    """扫描重拍 × 预滚, 输出候选表。
    🚨 scan_dur 要覆盖**全曲** —— 只扫前 200s 会漏掉后半段的高潮区,
       而曲子后半段常常才是 outro 的好素材。
    🚨 max_beats 取最强的 N 个但**按时间排序**保留全曲分布,
       否则只会在曲子开头扎堆。
    """
    peaks = onset_times(path, 0.0, scan_dur)
    if not peaks:
        return []
    # 按强度取最强的 max_beats 个, 再按时间排 → 保留全曲分布
    strongest = sorted(peaks, key=lambda p: -p[0])[:max_beats]
    peaks = sorted(strongest, key=lambda p: p[1])

    # 曲子满能量基准: 全曲 RMS 的 95 分位 → 用来归一化 head100
    full = read_wav(path, 0.0, scan_dur)
    if len(full) >= 1024:
        seg = 1600   # 100ms @16k
        rmss = []
        for i in range(0, len(full) - seg, seg):
            s = full[i:i + seg]
            rmss.append(20 * np.log10(np.sqrt((s ** 2).mean()) + 1e-9))
        ref_head100 = float(np.percentile(rmss, 95)) if rmss else -6.0
    else:
        ref_head100 = -6.0

    rows = []
    for strength, beat in peaks:
        for pre in prers:
            cut = beat - pre
            if cut < 0:
                continue
            x = read_wav(path, cut, max(intro_len, outro_len))
            if len(x) < 1024:
                continue
            voice_db = band_density_db(x, *BAND_VOICE)
            impact_db = band_density_db(x, *BAND_IMPACT)
            h100 = head_energy(path, cut, 0.1)
            # 满能量切入判定: 前 100ms 与后 1s 的差距越小越"直接"
            h_rest = head_energy(path, cut + 0.3, 1.0)
            rows.append({
                "cut": round(cut, 3),
                "beat": round(beat, 3),
                "pre_roll": round(pre, 3),
                "onset_strength": round(strength, 3),
                "voice_band_db": round(voice_db, 2),
                "impact_db": round(impact_db, 2),
                "head100_dbfs": round(h100, 2),
                "head_vs_rest_db": round(h100 - h_rest, 2),
                # 满能量切入度: 0 = 满能量, 负 = 差多少 dB
                "head_fill": round(h100 - ref_head100, 2),
                "ref_head100": round(ref_head100, 2),
            })
    return rows


def pick(rows, need_len, want_impact=False, exclude_near=0.0, exclude_at=None):
    """从候选里挑最佳。
    want_impact=True (片头): 优先"满能量切入 + 冲击强 + 人声带低"
    want_impact=False (片尾): 优先"人声带低 + 冲击够" (片尾是收束, 不追求爆)
    exclude_near: 排除与 exclude_at 相距小于该秒数的候选
                  (intro/outro 必须错开, 否则听到同一段旋律)
    """
    if not rows:
        return None
    pool = rows
    if exclude_at is not None and exclude_near > 0:
        filtered = [r for r in rows if abs(r["cut"] - exclude_at) >= exclude_near]
        if filtered:
            pool = filtered
    scored = []
    for r in pool:
        # 归一化打分
        v = -r["voice_band_db"]          # 人声带越低越好
        i = r["impact_db"]               # 冲击越高越好
        # 🎯 "满能量切入" 的正确判据是 **head_fill**(首 100ms 相对曲子满能量基准的差),
        #    不是 "首 vs 后" 的差值 ——
        #    差值大反而意味着切入后音乐马上变安静(比如踩在弱位上), 不是高潮。
        #    实测: head_vs_rest=+4.07 的点首 100ms 只有 -7.39dBFS,
        #          head_vs_rest=+2.17 的点首 100ms 反而是 -8.45dBFS(更满)。
        h = r.get("head_fill", -99.0)       # 越接近 0 越好
        if want_impact:
            score = 0.35 * v + 0.20 * i + 0.45 * h
        else:
            score = 0.65 * v + 0.35 * i
        scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    return scored[0][1]


def ast_probe(path, cut, dur):
    """可选: AST 打该片段的标签(需要 torch+transformers)。"""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        M = "MIT/ast-finetuned-audioset-10-10-0.4593"
        ex = AutoFeatureExtractor.from_pretrained(M)
        model = AutoModelForAudioClassification.from_pretrained(M)
        x = read_wav(path, cut, dur)
        hop = SR * 5
        hits = 0
        chunks = 0
        music = 0.0
        HOT = ["Bass drum", "Drum roll", "Electronic music", "Techno", "Dance music",
               "Drum", "Snare drum", "Beat", "Rock music", "Bass drum hit"]
        for i in range(0, max(1, len(x) - hop), hop):
            seg = x[i:i + hop]
            if len(seg) < 4000:
                continue
            inp = ex(seg, sampling_rate=SR, return_tensors="pt")
            with torch.no_grad():
                p = torch.sigmoid(model(**inp).logits)[0].numpy()
            idx = np.argsort(-p)[:12]
            labels = {model.config.id2label[j]: float(p[j]) for j in idx}
            chunks += 1
            music += labels.get("Music", 0.0)
            if any(h in labels for h in HOT):
                hits += 1
        return {"ast_impact_hit": f"{hits}/{chunks}", "ast_music": round(music / max(1, chunks), 3)}
    except Exception as e:
        return {"ast_error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bgm")
    ap.add_argument("--intro-len", type=float, default=24.36)
    ap.add_argument("--outro-len", type=float, default=22.60)
    ap.add_argument("--body-len", type=float, default=206.36)
    ap.add_argument("--preroll", default="0,0.05,0.10,0.15,0.20,0.25,0.30")
    ap.add_argument("--ast", action="store_true", help="对最终选点跑 AST")
    ap.add_argument("--json", default="")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    path = pathlib.Path(args.bgm)
    prers = [float(x) for x in args.preroll.split(",")]

    print(f"=== 分析 {path.name} ===")
    rows = scan_candidates(path, args.intro_len, args.outro_len, prers)
    if not rows:
        print("❌ 没找到候选(曲子太短或 onset 太少)")
        return

    rows.sort(key=lambda r: -(0.35 * (-r["voice_band_db"]) + 0.20 * r["impact_db"]
                             + 0.45 * r.get("head_fill", -99.0)))
    print(f"\n=== 候选 Top {args.top} (按片头综合分) ===")
    print(f"{'切入':>9} {'预滚':>6} {'人声带':>8} {'冲击':>7} {'首100ms':>9} {'满能量差':>9}")
    for r in rows[:args.top]:
        print(f"{r['cut']:9.3f} {r['pre_roll']:6.2f} {r['voice_band_db']:8.2f} "
              f"{r['impact_db']:7.2f} {r['head100_dbfs']:9.2f} {r.get('head_fill', -99):9.2f}")

    intro = pick(rows, args.intro_len, want_impact=True)
    # 🚨 intro 与 outro 必须**错开** —— 否则听到同一段旋律。
    #   排除 intro 附近 ±(intro_len + outro_len) 的候选。
    gap = max(args.intro_len, args.outro_len) + 5.0
    outro = pick(rows, args.outro_len, want_impact=False,
                  exclude_near=gap, exclude_at=intro["cut"] if intro else None)

    # 正文: 找连续驱动段 —— 人声带低 + 冲击够 + 与 intro/outro 都不重叠
    body = None
    body_pool = [r for r in rows
                 if not (intro and abs(r["cut"] - intro["cut"]) < 30)
                 and not (outro and abs(r["cut"] - outro["cut"]) < 30)]
    for r in body_pool:
        if r["voice_band_db"] < -2.5 and r["impact_db"] > 6.0:
            body = r
            break
    if body is None and body_pool:
        body = min(body_pool, key=lambda r: r["voice_band_db"])

    result = {"bgm": path.name, "intro": intro, "outro": outro, "body": body,
              "candidates": rows[:args.top]}
    if args.ast:
        for k in ("intro", "outro"):
            if result[k]:
                result[k].update(ast_probe(path, result[k]["cut"],
                                            args.intro_len if k == "intro" else args.outro_len))

    print("\n=== 推荐取材点 ===")
    for k in ("intro", "body", "outro"):
        r = result[k]
        if r:
            print(f"  {k:<6} cut={r['cut']:8.3f}s  人声带 {r['voice_band_db']:+.2f}dB  "
                  f"冲击 {r['impact_db']:+.2f}dB  首100ms {r['head100_dbfs']:+.2f}dBFS")
        else:
            print(f"  {k:<6} (未找到)")
    if result["intro"] and result["outro"] and abs(result["intro"]["cut"] - result["outro"]["cut"]) < 30:
        print("  ⚠️ intro/outro 取材点太近, 会听到同一段旋律 → 手动错开")

    print("\n→ 写入 mix 配置:")
    for k in ("intro", "body", "outro"):
        if result[k]:
            print(f'  SRC = {{"{k}": {result[k]["cut"]:.3f}}}')

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ {args.json}")


if __name__ == "__main__":
    main()
