#!/usr/bin/env python3
"""字幕后期 —— 切句 → 排版 → 渲染 PNG → 烧录 → 覆盖率验收。

🚨 三个必踩的坑(都不会报错, 只是画面难看):
  ① **字体 .ttc 的 index 陷阱**
     STHeiti Light.ttc: index 0 = Heiti **TC**(繁体), index 1 = Heiti **SC**(简体)。
     PIL 默认取 index 0 → 用繁体字形画简体字, 部分字形不对。
     Menlo.ttc: index 0 = Regular(不是 Bold!) → 不能一律用 index=1。
     → .ttc 必须显式指定 index, 且不同字体不同。
  ② **字体参数传错类型 → 静默回落 → 满屏豆腐块**
     写 `F(F, 25)` 而 F 其实是**函数**(不是路径) →
     `ImageFont.truetype(<function>, 25)` 抛 AttributeError →
     被 `except: return ImageFont.load_default()` 吞掉 →
     渲染出满屏缺字方框, **没有任何报错**。
     → 必须断言"传进去的是 str 路径"。
  ③ **竖排字形被当横排用**
     某些渲染路径返回字符串时要转成**行列表**, 否则整条挤成一行。

🚨 断句必须切在**标点**处:
     「先说清楚：它不是一个成品软件，是一个模块一个模块搭起来的。」
     按 20 字硬折 → 「…是一个模块」/「一个模块搭起来的。」→ 视觉上像重复。
     → 标点优先于长度: 在标点候选里选**让最长行最短**的那个,
       只要最长行 ≤ max_chars*1.25 就接受。

用法:
    python subtitle_post.py --align align.json --script script.json \
        --timeline timeline.json --video silent.mp4 --out final.mp4
    python subtitle_post.py --align align.json --script script.json \
        --timeline timeline.json --verify-only --video final.mp4
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

# 字体候选: (路径, ttc index)
FONT_CANDIDATES = [
    ("/System/Library/Fonts/STHeiti Medium.ttc", 1),      # index1 = 简体
    ("/System/Library/Fonts/STHeiti Light.ttc", 1),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
]

# 等宽英文/数字字体(代码片段用)
MONO_CANDIDATES = [
    ("/System/Library/Fonts/Menlo.ttc", 0),               # index0 = Regular
    ("/System/Library/Fonts/SFMono.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
]

SENT_END = "。！？；…"
SOFT_END = "，、：,"
MAX_CHARS = 20
LONGEST_RATIO = 1.25


def pick_font(candidates=None):
    """返回 (path, index) —— 必须是 str 路径 + int index。"""
    for path, idx in (candidates or FONT_CANDIDATES):
        if pathlib.Path(path).exists():
            return path, idx
    return None, None


def get_font(size, mono=False):
    """🚨 坑②: 断言传进去的是 str 路径, 否则会静默回落成豆腐块。"""
    from PIL import ImageFont
    path, idx = pick_font(MONO_CANDIDATES if mono else FONT_CANDIDATES)
    if not path:
        raise RuntimeError("没找到可用字体")
    assert isinstance(path, str) and path, "字体路径必须是 str"
    try:
        return ImageFont.truetype(path, size, index=idx)
    except Exception as e:
        raise RuntimeError(f"字体加载失败 {path} index={idx}: {e}") from e


def split_sentences(text, max_chars=MAX_CHARS):
    """按句末标点断句; 长句再按逗号断; 每段尽量 ≤ max_chars。"""
    text = (text or "").strip()
    if not text:
        return []
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in SENT_END:
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)

    final = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
            continue
        # 长句 → 按软标点再断
        chunks, cur = [], ""
        for ch in p:
            cur += ch
            if ch in SOFT_END and len(cur) >= max_chars * 0.6:
                chunks.append(cur)
                cur = ""
        if cur.strip():
            chunks.append(cur)
        for c in chunks:
            if len(c) <= max_chars:
                final.append(c)
            else:
                # 硬折(兜底)
                for i in range(0, len(c), max_chars):
                    final.append(c[i:i + max_chars])
    # 🚨 合并过短的碎条 —— 硬折常留下 2~3 字的尾巴("搭起来" + "的。"),
    #    观众看到的是两条字幕闪一下, 很难看。
    #    规则: 尾巴 < 6 字且前一条 ≤ max_chars*1.25 → 合并回去。
    merged = []
    for seg in final:
        if (merged and len(seg) < 6
                and len(merged[-1]) + len(seg) <= int(max_chars * LONGEST_RATIO)):
            merged[-1] = merged[-1] + seg
        else:
            merged.append(seg)
    return [s for s in merged if s.strip()]


def wrap_two_lines(s, max_chars=MAX_CHARS):
    """单条字幕排成最多 2 行, **在标点处断**, 标点优先于长度。"""
    if len(s) <= max_chars:
        return [s]
    limit = max_chars * LONGEST_RATIO
    # 候选断点: 标点后
    cands = [i + 1 for i, ch in enumerate(s) if ch in SOFT_END + SENT_END]
    best, best_score = None, None
    for i in cands:
        a, b = s[:i], s[i:]
        score = max(len(a), len(b))
        if best_score is None or score < best_score:
            best, best_score = (a, b), score
    if best and best_score <= limit:
        return list(best)
    # 标点不满足 → 硬折
    mid = (len(s) + 1) // 2
    return [s[:mid], s[mid:]]


def _norm_index(text):
    return re.sub(r"\s+", "", text or "")


def _sentence_spans(text):
    """按句末标点切, 返回 [(start, end)] 字符下标。"""
    spans, start = [], 0
    for i, ch in enumerate(text or ""):
        if ch in SENT_END:
            spans.append((start, i + 1))
            start = i + 1
    if start < len(text or ""):
        spans.append((start, len(text)))
    return spans


def build_cues(align, script_items, max_chars=MAX_CHARS):
    """从 ASR 对齐轴 + 脚本原文构造字幕 cue。
    时间取 ASR(真实语音), 文字取脚本原文(零错字)。"""
    items = script_items if isinstance(script_items, list) else script_items.get("segments", [])
    # align 结构: {seg_key: [{"word":..,"start":..,"end":..}]} 或扁平列表
    cues = []
    for it in items:
        key = it.get("key") or it.get("id") or it.get("title")
        text = it.get("text", "")
        words = align.get(key) if isinstance(align, dict) else None
        if not words:
            words = align.get("words") if isinstance(align, dict) else None
        if not words:
            continue
        chars = []
        for w in words:
            wtxt = w.get("word", w.get("text", ""))
            st = w.get("start", 0.0)
            en = w.get("end", st)
            for c in wtxt:
                chars.append((c, st, en))
        # 脚本原文的字符序列(去空白) → 与 ASR 字符对齐
        script_chars = [c for c in _norm_index(text)]
        if not script_chars or not chars:
            continue
        # 简单对齐: 逐字符取 ASR 时间, 长度不一致时按比例缩放
        n_asr = len(chars)
        n_sc = len(script_chars)
        for sent in split_sentences(text, max_chars):
            s_norm = _norm_index(sent)
            if not s_norm:
                continue
            # 在脚本里的位置
            pos = _norm_index(text).find(s_norm)
            if pos < 0:
                continue
            frac0 = pos / max(n_sc, 1)
            frac1 = (pos + len(s_norm)) / max(n_sc, 1)
            i0 = min(n_asr - 1, int(frac0 * n_asr))
            i1 = min(n_asr - 1, max(i0, int(frac1 * n_asr) - 1))
            st = chars[i0][1]
            en = chars[i1][2]
            if en <= st:
                en = st + 0.4
            cues.append((st, en, wrap_two_lines(sent, max_chars)))
    cues.sort(key=lambda c: c[0])
    return cues


def render_png(lines, out_path, width=1920, height=1080, font_size=44, margin_v=60):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    font = get_font(font_size)
    line_h = int(font_size * 1.35)
    total_h = line_h * len(lines)
    y = height - margin_v - total_h
    for ln in lines:
        bbox = dr.textbbox((0, 0), ln, font=font)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        # 描边(1~2px 足够, 过重会显糊)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            dr.text((x + dx, y + dy), ln, font=font, fill=(0, 0, 0, 220))
        dr.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(out_path)


def burn(video, cues, out, work_dir, font_size=44, margin_v=60):
    """用 ffmpeg overlay 逐条烧字幕(不做二次编码整片 —— 会把顶栏小字压糊)。"""
    work = pathlib.Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    pngs = []
    for i, (st, en, lines) in enumerate(cues):
        p = work / f"sub_{i:04d}.png"
        render_png(lines, p, font_size=font_size, margin_v=margin_v)
        pngs.append((st, en, p))
    if not pngs:
        print("⚠️ 没有字幕可烧")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                        "-c", "copy", str(out)], check=True)
        return out
    fc, cur = [], "0:v"
    for i, (st, en, p) in enumerate(pngs):
        nxt = f"v{i}"
        fc.append(f"[{cur}][{i}:v]overlay=0:0:enable='between(t,{st:.3f},{en:.3f})'"
                  f"[{nxt}]")
        cur = nxt
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    for _, _, p in pngs:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v0]", "-map", "0:a?",
            "-c:a", "copy", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            str(out)]
    subprocess.run(cmd, check=True)
    return out


def verify_coverage(video, cues, timeline, sample_step=0.5):
    """抽帧检测字幕像素, 算覆盖率。
    ⚠ 必须排除**无字幕区间**: title_card / intro / outro / 段尾静音尾巴。
       只排除 title_card 会误判成"成片没字幕"。"""
    tl = json.loads(pathlib.Path(timeline).read_text())
    total = tl["total"]
    excluded = []
    for t in tl.get("tracks", []):
        if t.get("kind") in ("title_card", "intro", "outro"):
            excluded.append((t["start"], t["end"]))
    # 段尾静音尾巴: 段落结束前 0.6s
    for t in tl.get("tracks", []):
        if t.get("kind") == "segment":
            excluded.append((max(0, t["end"] - 0.6), t["end"]))

    def is_excluded(ts):
        return any(a - 1e-6 <= ts <= b + 1e-6 for a, b in excluded)

    # 字幕应出现的区间
    expected = [(s, e) for s, e, _ in cues]
    covered = 0
    checked = 0
    for i in range(int(total / sample_step)):
        ts = i * sample_step
        if is_excluded(ts):
            continue
        want = any(s - 1e-6 <= ts <= e + 1e-6 for s, e in expected)
        if not want:
            continue
        checked += 1
        # 抽帧检测字幕区像素
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(video),
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True)
        if not r.stdout:
            continue
        if _has_bright_bottom(r.stdout):
            covered += 1
    pct = covered / max(checked, 1) * 100
    print(f"字幕覆盖率 = {pct:.2f}%  ({covered}/{checked} 个采样点)")
    print("✅ 合格 (≥90%)" if pct >= 90 else "❌ 不合格 (<90%)")
    return pct


def _has_bright_bottom(png_bytes, thresh=200):
    """检测图像底部区域是否有亮像素(字幕是白字)。"""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = img.size
        region = img.crop((0, int(h * 0.78), w, h))
        px = region.getdata()
        bright = sum(1 for v in px if v > thresh)
        return bright > 40
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--align", default="", help="ASR 对齐轴 JSON")
    ap.add_argument("--script", default="", help="脚本 JSON(含 text 字段)")
    ap.add_argument("--timeline", default="", help="timeline.json")
    ap.add_argument("--video", default="", help="待烧录视频")
    ap.add_argument("--out", default="", help="输出")
    ap.add_argument("--work-dir", default="")
    ap.add_argument("--font-size", type=int, default=44)
    ap.add_argument("--margin-v", type=int, default=60)
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--font-check", action="store_true", help="只做字体自查")
    args = ap.parse_args()

    if args.font_check:
        path, idx = pick_font()
        print(f"中文字体: {path} index={idx}")
        f = get_font(args.font_size)
        print(f"加载 OK: {type(f).__name__}")
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (400, 80), (0, 0, 0))
            ImageDraw.Draw(img).text((10, 10), "中文渲染测试 OK", font=f, fill=(255, 255, 255))
            out = "/tmp/_font_test.png"
            img.save(out)
            print(f"渲染测试图 → {out}")
        except Exception as e:
            print(f"❌ 渲染失败: {e}")
            return 1
        return 0

    if not (args.align and args.script):
        print("需要 --align 和 --script")
        return 1

    align = json.loads(pathlib.Path(args.align).read_text())
    script = json.loads(pathlib.Path(args.script).read_text())
    cues = build_cues(align, script, args.max_chars)
    print(f"字幕 {len(cues)} 条")
    for s, e, lines in cues[:6]:
        print(f"  {s:7.2f}s → {e:7.2f}s  {' / '.join(lines)}")
    if len(cues) > 6:
        print(f"  ... 共 {len(cues)} 条")

    if args.verify_only:
        if args.video and args.timeline:
            verify_coverage(args.video, cues, args.timeline)
        return 0

    if not (args.video and args.out):
        print("需要 --video 和 --out")
        return 1
    wd = args.work_dir or "/tmp/_sub_work"
    burn(args.video, cues, args.out, wd, args.font_size, args.margin_v)
    print(f"✅ 烧录完成 → {args.out}")
    if args.timeline:
        verify_coverage(args.out, cues, args.timeline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
