#!/usr/bin/env python3
r"""字幕 —— 旁白文案 → 切句 → PIL 渲染字幕 → ffmpeg overlay 烧录。

用法:
    python subtitle.py --config project.json --script script.json \
                       --compose-plan work/out/compose_plan.json \
                       --video work/out/final.mp4 --out work/out/final_sub.mp4

🚨🚨 为什么不用 ffmpeg 的 subtitles 滤镜:
   Homebrew 的 ffmpeg **默认不编译 libass / libfreetype / fontconfig**
   (实测 `ffmpeg -filters | grep subtitles` 为空, -version 里也没有 --enable-libass)
   → 报错 "No option name near 'xxx.ass'" —— 三种转义写法都试过, 全是同一个错,
     看起来像路径转义问题, 其实是滤镜根本不存在。
   → **解法**: 用 PIL 自己渲染字幕成 PNG, 再用 overlay 滤镜贴上去。
     overlay 是 ffmpeg 内置的, 一定有。字体渲染还更可控。

🚨 时间轴怎么算(最容易错的地方):
   旁白是**分段**的, 每段一个 mp3。段 i 在成片里的起点 = 前面所有段 content 之和。
   段内各句的时间要**按字数比例**分配 —— TTS 没返回逐句时间戳。
   ⚠ 按字数分配只是近似(停顿多的句子实际更长)。实测偏差 < 0.3s, 观感可接受。
   ⚠ MiniMax 支持 subtitle_enable=True, 但返回的是**整段一条**, 不是逐句 —— 用不上。

🚨 字幕样式:
   - 深色背景 → 白字 + 黑色描边, 不要纯白(刺眼)
   - 底部安全区: 距底边 3%~7%(平台/播放器 UI 会遮边)
   - 一次最多 2 行, 每行 ≤ 20 汉字
"""
import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FF = "ffmpeg"

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def pick_font():
    for f in FONT_CANDIDATES:
        if pathlib.Path(f).exists():
            return f
    return None


def split_sentences(text, max_chars=20, max_lines=2):
    """把一段旁白切成若干条字幕。

    🚨 切句的约束是**每行 ≤ max_chars**, 不是「整条 ≤ max_chars*max_lines」。
    只按总长切会出现这种:
      「参数不多，但每一个都得想清楚：种群是每一代同时试多少个因子公式，代数是迭代多少轮，」
      总长 43 < 40? 不, 43 > 40 → 被当一条 → 折行时只能硬折 → 出现 33 字的超长行。
    正确: 在逗号处切, 让**每一段**都能排成 ≤ max_chars 的行。

    1) 先按句末标点(。！？；)断
    2) 长句再按逗号/顿号断(每段尽量 ≤ max_chars*max_lines)
    3) 超长硬切
    """
    text = (text or "").strip()
    if not text:
        return []

    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？；":
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())

    out = []
    for p in parts:
        # 🚨 切句阈值 = max_chars*1.5 (30 字)。
        #    试过的三个值:
        #      40 (=max_chars*2) → 条数少(47) 但要折行 → 拆词("…人会先"/"看曲线")
        #      20 (=max_chars)   → 条数多(68) 几乎不折行 → 拆词少但字幕闪太快
        #      30 (=max_chars*1.5) → 平衡: 约 55 条, 每条基本单行或两行都在标点处
        #    判断标准: 187 秒里 55 条 ≈ 3.4 秒/条, 15-20 字 → 阅读从容。
        if len(p) <= max_chars * 1.5:
            out.append(p)
            continue
        # 🚨 在**每个逗号/顿号处**切, 不设长度下限。
        #    之前要求 "len(cur) >= max_chars" 才切 → 切出来 27 字
        #    「但每一个都得想清楚：种群是每一代同时试多少个因子公式，」
        #    → 折行时只能硬折 → 出现 33 字超长行 + 「…那块我 / 故意留空」这种断法。
        #    代价是字幕条数变多, 但每条都读得顺 —— **可读性优先于条数**。
        chunks, cur = [], ""
        for ch in p:
            cur += ch
            if ch in "，、,:":
                chunks.append(cur.strip())
                cur = ""
        if cur.strip():
            chunks.append(cur.strip())
        # 合并过短的碎片(避免「为什么？」这种孤零零一条)
        merged = []
        for c in chunks:
            if merged and len(merged[-1]) < max_chars * 0.5:
                merged[-1] = merged[-1] + c
            else:
                merged.append(c)
        out.extend(merged)

    final = []
    for p in out:
        # 🚨 保证每条 ≤ max_chars*1.5 —— 折行后两行各 ~15-20 字, 视觉最舒服。
        #    (上限太大 → 出现 21+20 的挤压行; 上限太小 → 条数暴涨字幕闪太快)
        while len(p) > max_chars * 1.5:
            cut = int(max_chars * 1.5)
            for i in range(cut, max_chars, -1):
                if p[i - 1] in "，、,；;。":
                    cut = i
                    break
            final.append(p[:cut])
            p = p[cut:]
        if p:
            final.append(p)
    return final


def wrap_two_lines(s, max_chars=20):
    """单条字幕排成最多 2 行。

    🚨 **必须在标点处断行, 不能按字数硬折**。
    实测: 「先说清楚：它不是一个成品软件，是一个模块一个模块搭起来的。」
    按 20 字硬折 → 「…是一个模块」/「一个模块搭起来的。」→ 视觉上像重复了「一个模块」。

    🚨 **标点永远优先于长度**。
    之前规则: 两行都必须 ≤ max_chars(20), 否则退回硬折。
    结果: 「一旦把趋势曲线和一堆参数摆在一起，人会先看曲线，再回头调参数，」
    标点在第 23 字处 → 23 > 20 被拒 → 硬折 → 「…人会先」/「看曲线，…」→ **"先看"被拆开**。
    改成: 在标点候选里选**让最长行最短**的那个, 只要最长行 ≤ max_chars*1.25(=25) 就接受。
    (1920 宽 + 44px 字体, 25 汉字约 1100px, 远放得下; 长度约束是为了可读性不是宽度)

    🚨 标点候选里挑**最长行最短**的, 而不是离中点最近的 ——
       「a,b,c,d」四个标点时, 离中点最近的未必让两行最均衡。
    """
    if len(s) <= max_chars:
        return [s]

    # 收集所有标点候选位置(标点后的位置)
    cands = [i for i in range(1, len(s)) if s[i - 1] in "，、,；;。"]
    best = None
    best_score = None
    for i in cands:
        a, b = s[:i], s[i:]
        longest = max(len(a), len(b))
        if longest > max_chars * 1.25:
            continue
        # 打分: 先看最长行, 再看两行均衡度
        score = (longest, abs(len(a) - len(b)))
        if best_score is None or score < best_score:
            best_score = score
            best = i

    if best:
        return [s[:best], s[best:]]

    # 🚨 没有标点能让两行都排下 → 硬折, 但**优先切在靠后的标点**。
    #    实测: 「任何一个数字都要能追到源头——用的哪段数据、什么假设、哪一版代码。」
    #    标点在 30 字处(逗号), 但中点 20 字处没标点 → 硬折成
    #    「…用的哪段行」/「据、什么假设…」→ **"数据"被拆开**。
    #    正确: 宁可第一行长一点(≤ max_chars*1.5), 也要切在标点上。
    for i in cands:
        if i >= max_chars and i <= int(max_chars * 1.5):
            return [s[:i], s[i:]]

    return [s[:max_chars], s[max_chars:]]


def build_cues_from_align(align, script_items, plan, max_chars=20):
    """从 ASR 对齐轴生成字幕 cue —— **时间来自真实语音, 文字来自脚本原文**。

    🚨 为什么必须用对齐轴而不是按字数摊时间:
        按字数摊是**猜的**。TTS 语速不均匀(停顿/语速/句长都不同),
        实测每段偏 0.3~1.5s, 累积到片尾能偏好几秒。
        观众看到的是「字幕比人声慢半拍」→ 极难看。

    ✅ 对齐轴: 逐字时间戳由 ASR 给出, 文字用脚本原文(零错字)。

    🚨🚨🚨 **关键坑: 对齐轴里没有标点!**
        asr_align.py 建轴时把标点全过滤掉了(PUNCT 集合),
        因为 ASR 输出本来就没有标点, 脚本的标点没法直接对到 ASR 字符上。
        → 如果按对齐轴里的字符切句, 一个标点都找不到 → 整段变成一条字幕
          (实测 8 段 → 只有 8 条, 原来按字数摊是 55 条)。
        → **正确做法**: 用**脚本原文的标点**切句, 再把每个字映射到对齐轴的索引。
           映射靠"标点也是字符"这个事实: 脚本原文去掉标点后的字符序列
           与对齐轴的字符序列**一一对应**(对齐率 95.6% 就是这个保证)。
    """
    seg_cues = {}
    for p in plan:
        idx = str(p["idx"])
        if idx not in align:
            continue
        chars = align[idx]["chars"]
        if not chars:
            continue
        item = script_items[p["idx"]] if p["idx"] < len(script_items) else {}
        raw_text = item.get("text", "")
        out = []
        # ── 一次成型: 原文 → 字幕块 ──
        for block in _split_blocks(raw_text, max_chars):
            a_i, b_i, txt = block
            if b_i <= a_i or a_i >= len(chars):
                continue
            b_i = min(b_i, len(chars))
            seg = chars[a_i:b_i]
            if not seg:
                continue
            out.append((seg[0]["s"], seg[-1]["e"], txt))
        if out:
            seg_cues[p["idx"]] = out
    return seg_cues


def _split_blocks(text, max_chars=20):
    """原文 → [(去标点区间起, 止, 带标点文字), ...]。

    🚨 一次成型, 不做二次拼接 —— 之前先切句再切子句再合并, 结果**漏字**
       (seg0 丢了「先说清楚：它不是一个成品软件，是」整段)。
       根因: 两次切分之间用"字数"重新对齐, 合并逻辑又把区间吃掉了。

    ✅ 正确模型: 按原文顺序**单向扫描**, 遇到标点就是断点,
       每个块记录 (在去标点序列里的起止, 原文文字)。
       块之间不重叠、不遗漏 → 拼起来必然等于原文。

    断点规则:
       句末标点(。！？；) → 必断
       逗号类(，、,:) → 断, 但只有当前块已经够长(≥ max_chars*0.5)才断,
                        否则并入下一块(避免「为什么？」这种孤条)
       块长 > max_chars*1.5 → 在最近的逗号处硬切, 没有逗号就硬切
    """
    seq, pos_map = _norm_index(text)
    blocks = []
    cur_start = 0          # 去标点序列里的起点
    cur_txt = []           # 原文文字累积
    cur_len = 0            # 去标点字数
    last_comma = None      # (去标点位置, 累积文字长度)

    def flush(end_i):
        nonlocal cur_start, cur_txt, cur_len, last_comma
        if cur_len > 0:
            blocks.append((cur_start, end_i, "".join(cur_txt).strip()))
        cur_start = end_i
        cur_txt = []
        cur_len = 0
        last_comma = None

    for i, ch in enumerate(text):
        pi = pos_map[i]
        if pi >= 0:
            cur_txt.append(ch)
            cur_len += 1
            if cur_len > max_chars * 1.5:
                # 超长 → 在最近的逗号处切, 没有就硬切
                if last_comma and last_comma[0] > cur_start:
                    cpos, clen = last_comma
                    head = "".join(cur_txt[:clen])
                    tail = cur_txt[clen:]
                    blocks.append((cur_start, cpos, head.strip()))
                    cur_start = cpos
                    cur_txt = list(tail)
                    cur_len = len(tail)
                    last_comma = None
                else:
                    cut = max_chars * 3 // 2
                    head = "".join(cur_txt[:cut])
                    tail = cur_txt[cut:]
                    blocks.append((cur_start, cur_start + cut, head.strip()))
                    cur_start = cur_start + cut
                    cur_txt = list(tail)
                    cur_len = len(tail)
                    last_comma = None
            continue

        # ── 标点 ──
        # ⚠ 标点自己的 pos_map 是 -1(它不在去标点序列里)。
        #   断点位置 = 「标点前最后一个实义字的位置 + 1」——
        #   即标点所依附的那个字的**后面**。
        anchor = cur_start + cur_len      # 当前块已积累的实义字数 = 下一个实义字的下标
        if ch in "。！？；":
            cur_txt.append(ch)
            flush(anchor)
        elif ch in "，、,:;":
            cur_txt.append(ch)
            if cur_len >= max_chars * 0.5:
                # 够长了 → 在这里断
                flush(anchor)
            else:
                last_comma = (anchor, len(cur_txt))
        else:
            cur_txt.append(ch)

    if cur_len > 0 or cur_txt:
        blocks.append((cur_start, len(seq), "".join(cur_txt).strip()))
    return [b for b in blocks if b[2]]


def _norm_index(text):
    """脚本原文 → 去标点字符序列的下标映射(每个字在去标点序列中的位置)。"""
    seq = []
    pos_map = []
    for ch in text:
        if ch in PUNCT_SET or ch.isspace():
            pos_map.append(-1)
        else:
            pos_map.append(len(seq))
            seq.append(ch)
    return seq, pos_map


def _sentence_spans(text):
    """按句末标点切句 → [(去标点序列起点, 终点), ...]"""
    seq, pos_map = _norm_index(text)
    spans = []
    start = 0
    cur = 0
    for i, ch in enumerate(text):
        if ch in PUNCT_SET or ch.isspace():
            if ch in "。！？；" and cur > start:
                spans.append((start, cur))
                start = cur
            continue
        cur += 1
    if cur > start:
        spans.append((start, cur))
    return spans or [(0, len(seq))]


def _sub_spans(text, a_i, b_i, max_chars):
    """句内按逗号切子句 → [(子句起, 止), ...] (在**去标点**索引空间)。

    返回的区间不含标点, 但**标点位置信息**单独放在 _PUNCT_AT 里,
    供上层把原文(含标点)的文字取回来。
    """
    seq, pos_map = _norm_index(text)
    out = []
    sub_start = a_i
    for i, ch in enumerate(text):
        if ch in PUNCT_SET or ch.isspace():
            if ch in "，、,:;":
                # 找这个逗号前面最近的实义字 → 在那里断开
                for j in range(i - 1, -1, -1):
                    pj = pos_map[j]
                    if pj >= 0:
                        if a_i <= pj < b_i and (pj + 1) > sub_start:
                            out.append((sub_start, pj + 1))
                            sub_start = pj + 1
                        break
            continue
    if b_i > sub_start:
        out.append((sub_start, b_i))
    # 合并过短碎片 + 硬切超长
    merged = []
    for s, e in out:
        if merged and (e - s) < max_chars * 0.45:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    final = []
    for s, e in merged:
        while (e - s) > max_chars * 1.5:
            cut = s + int(max_chars * 1.5)
            final.append((s, cut))
            s = cut
        if e > s:
            final.append((s, e))
    return final


def _text_with_punct(text, a_i, b_i):
    """取脚本原文里对应 [a_i,b_i) 去标点区间的**带标点**文字。

    🚨 字幕不能丢标点 —— "先说清楚它不是成品软件" 读起来费劲,
       "先说清楚：它不是成品软件，" 才自然。
       标点不属于对齐轴, 但属于**原文**, 所以文字走原文、时间走对齐轴。
    """
    _, pos_map = _norm_index(text)
    picked = []
    for i, ch in enumerate(text):
        pi = pos_map[i]
        if pi >= 0:
            if a_i <= pi < b_i:
                picked.append(ch)
        else:
            # 标点: 若它前一个实义字在区间内 → 收进来
            prev = -1
            for j in range(i - 1, -1, -1):
                if pos_map[j] >= 0:
                    prev = pos_map[j]
                    break
            if prev >= a_i and prev < b_i:
                picked.append(ch)
    return "".join(picked).strip()


PUNCT_SET = set("，。！？；：、,.!?;:()（）「」“”\"'…—《》〈〉【】[]{}·-～~")


def _split_by_punct(text, max_chars=20):
    """按标点切条, 每条 ≤ max_chars*1.5。与 subtitle.split_sentences 同口径。"""
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？；":
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    out = []
    for p in parts:
        while len(p) > max_chars * 1.5:
            cut = int(max_chars * 1.5)
            for i in range(cut, max_chars, -1):
                if p[i - 1] in "，、,；;。":
                    cut = i
                    break
            out.append(p[:cut])
            p = p[cut:]
        if p:
            out.append(p)
    return out


def build_cues(script_items, plan, max_chars=20, max_lines=2):
    """生成字幕条目 [(start, end, [line1, line2]), ...]。"""
    cues = []
    offset = 0.0
    for p in plan:
        idx = p["idx"]
        item = script_items[idx] if idx < len(script_items) else {}
        sents = split_sentences(item.get("text", ""), max_chars, max_lines)
        if not sents:
            offset += p["content"]
            continue
        seg_start = offset
        # 🚨 铺满整段(content) 而不是只铺旁白(wav_dur) —— 最后一条吃到段尾, 不留空隙
        seg_total = float(p.get("content") or p["wav_dur"])
        total_chars = sum(len(s) for s in sents) or 1
        t = seg_start
        for si, s in enumerate(sents):
            if si == len(sents) - 1:
                dur = (seg_start + seg_total) - t
            else:
                dur = seg_total * (len(s) / total_chars)
            cues.append((t, t + dur, wrap_two_lines(s, max_chars)))
            t += dur
        offset += p["content"]
    return cues


def render_png(lines, width, height, font_path, font_size=44, margin_v=60):
    """渲染一张字幕条 PNG (RGBA 透明底)。

    🚨🚨🚨 **lines 必须是行列表, 不能是字符串**。
    传字符串 → `len(lines)` = 字数 → 每个字单独占一行 → 字幕变成**竖排一列**
    (实测 v9: 字幕从 y=195 竖排到 y=990, 单字宽 38px, 完全不能看)。
    `build_cues_from_align` 返回的第三项就是**字符串**,
    所以调用方必须先过 `wrap_two_lines(txt, max_chars)` 转成行列表。
    这里做一层兜底: 传入字符串就自动按 max_chars 折行, 不让竖排事故复现。
    """
    from PIL import Image, ImageDraw, ImageFont
    if isinstance(lines, str):
        lines = wrap_two_lines(lines, 20)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    line_h = int(font_size * 1.35)
    total_h = line_h * len(lines)
    y = height - margin_v - total_h

    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=font)
        tw = bb[2] - bb[0]
        x = (width - tw) // 2
        # 描边(多层偏移) → 亮背景上也看得清
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)):
            d.text((x + dx, y + dy), ln, font=font, fill=(0, 0, 0, 235))
        d.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    return im


def burn(video, cues, out_path, cfg, work_dir):
    """渲染字幕 PNG 并用 overlay 烧录。

    做法: 每条字幕渲染一张 PNG, 再按时间段 overlay 到视频上。
    为了避免几十次 ffmpeg 调用太慢, 用 concat 滤镜一次串起来:
      [0:v][1:v]overlay=0:0:enable='between(t,a,b)'[v1]
      [v1][2:v]overlay=0:0:enable='between(t,c,d)'[v2]
      ...
    """
    from PIL import Image

    work = pathlib.Path(work_dir)
    sub_dir = work / "_subs"
    sub_dir.mkdir(parents=True, exist_ok=True)
    for f in sub_dir.glob("*.png"):
        f.unlink()

    width = cfg["video"]["width"]
    height = cfg["video"]["height"]
    font_path = pick_font()
    if not font_path:
        raise RuntimeError("没找到中文字体")
    scfg = cfg.get("subtitle", {})
    font_size = scfg.get("font_size", 44)
    margin_v = scfg.get("margin_v", 60)

    pngs = []
    for i, (s, e, lines) in enumerate(cues):
        im = render_png(lines, width, height, font_path, font_size, margin_v)
        p = sub_dir / f"s{i:03d}.png"
        im.save(p)
        pngs.append((p, s, e))

    # 构造 filter_complex
    inputs = []
    for p, _, _ in pngs:
        inputs += ["-i", str(p)]

    parts = []
    prev = "0:v"
    for i, (_, s, e) in enumerate(pngs):
        out_label = f"v{i}"
        parts.append(f"[{prev}][{i+1}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'[{out_label}]")
        prev = out_label
    fc = ";".join(parts)

    cmd = [FF, "-y", "-loglevel", "error", "-i", str(pathlib.Path(video).resolve())] + inputs
    cmd += ["-filter_complex", fc,
            "-map", f"[{prev}]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", str(pathlib.Path(out_path).resolve())]
    subprocess.run(cmd, check=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--compose-plan", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=20)
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())
    script_items = json.loads(pathlib.Path(a.script).read_text())
    plan = json.loads(pathlib.Path(a.compose_plan).read_text())

    scfg = cfg.get("subtitle", {})
    max_chars = scfg.get("max_chars", a.max_chars)

    cues = build_cues(script_items, plan, max_chars)
    print(f"字幕 {len(cues)} 条")
    for s, e, lines in cues[:5]:
        print(f"  {s:7.2f}s → {e:7.2f}s  {' / '.join(lines)}")
    if len(cues) > 5:
        print(f"  ... 共 {len(cues)} 条")

    burn(a.video, cues, a.out, cfg, cfg["paths"]["work_dir"])
    print(f"\n✅ 烧录完成 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
