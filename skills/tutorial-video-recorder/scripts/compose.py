#!/usr/bin/env python3
"""合成 —— 分段 webm + 旁白 → 横屏成片(带字幕 + 视觉层)。

用法:
    python compose.py --config project.json

产出:
    out/final.mp4          (横屏 1920x1080, 带字幕 + 标题卡 + 高亮框)
    out/compose_plan.json  (每段的时间轴)

🚨 音画对齐: **不要用 zoompan d= 猜**, 用**黑场标记**。
   录制时每段片头插 2s 纯黑, 这里用 PIL 数中心区域纯黑像素占比定位黑场结束点,
   旁白从那一刻开始播 → 像素级对齐。
   ⚠ blackdetect 对深色主题失效(站点本身就是深色, 纯黑分不出来) → 必须用 PIL 自己数。

🚨 响度: **必须实测**。漏加 loudnorm → −32 LUFS(人声几乎听不见),
   加上 → −16.2 LUFS。产出后跑 loudnorm print_format=summary 核对。

🚨 命名: 合成产物必须**清干净上一版**再生成。
   实测产出 v00.mp4 而下游读 v{i}.mp4 → 整片用的是上一轮旧文件,
   **规格完全正常、看规格发现不了**。

══════════════════════════════════════════════════════════════════════
🎬 视觉层(visual) —— 节奏与画面的执行者
══════════════════════════════════════════════════════════════════════
录屏类视频的通病是**死画面**: 界面静止 20 秒不动一个像素, 观众 3 秒划走。
实测(帧差量化, 见 scripts/pace.py):

    指标                    参考视频     我们(改造前)   目标
    变化强度(帧差均值)      6.2~19.4      0.87         ≥ 3.0
    变化帧/分钟             45~55         14.1         ≥ 30
    最长静止段              少            30s+         ≤ 6s
    静止画面占比            低            90%          ≤ 25%

四个参考视频的共通解法(全部照抄):
    1. **标题压屏**  —— 每段前插 2s 模糊底+超大粗衬线标题+荧光色关键词
    2. **高亮框**    —— 青绿圆角框圈住关键数字, 观众知道看哪
    3. **局部特写**  —— 角落插一个放大镜(PiP), 让细节看得见
    4. **推镜**      —— 整帧缓慢漂移, 每帧都在动, 死画面归零

🚨 **推镜必须做成「先缩放再裁剪窗口漂移」, 不能用 zoompan**:
   zoompan 的 d / fps / 时长三者的关系极易错, 实测会把 20s 的段拉成 60s。
   用 `scale=iw*Z:ih*Z` + `crop=W:H:x=...:y=...`(x/y 支持 t 表达式) → 时长天然正确。

🚨 **高亮框要画在「推镜之前」的坐标系里**:
   内容被推镜放大/平移后, 同一张框 PNG 的像素位置就对不上目标了。
   → 高亮框 overlay 必须在 push 滤镜**之前**, 让它跟着内容一起被推。
   (顺序: 0:v → 高亮框overlay → push → 字幕overlay)

🚨 **标题卡是独立片段, 插在段落之间**, 不占段落内的旁白时间:
   旁白从段落自己的 0s 开始, 标题卡在它**前面**静音播放。
   字幕覆盖率因此不受影响(段落内每一帧都有字幕)。

🚨 **标题卡底图用该段自己的画面** → 观众看得出延续性, 不会像突然黑屏。
"""
import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import subtitle  # noqa: E402
import visual   # noqa: E402

FF = "ffmpeg"
FP = "ffprobe"


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def dur(p):
    r = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def black_end_time(webm, tmpdir, fps=8, thresh=0.985):
    """找该段最后一个纯黑区间的结束时间(= 内容开始点)。"""
    tmpdir = pathlib.Path(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)
    for f in tmpdir.glob("*.jpg"):
        f.unlink()
    run([FF, "-y", "-loglevel", "error", "-i", str(webm),
         "-vf", f"fps={fps}", "-q:v", "3", str(tmpdir / "b%05d.jpg")])
    from PIL import Image
    step = 1.0 / fps
    rows = []
    for i, f in enumerate(sorted(tmpdir.glob("b*.jpg"))):
        im = Image.open(f).convert("RGB")
        w, h = im.size
        box = im.crop((int(w * 0.25), int(h * 0.25), int(w * 0.75), int(h * 0.75)))
        px = list(box.getdata())
        n = len(px)
        black = sum(1 for r, g, b in px if r < 14 and g < 14 and b < 14) / n
        rows.append((round(i * step, 3), black))
    best, cur = None, None
    for t, b in rows:
        if b >= thresh:
            cur = [t, t] if cur is None else [cur[0], t]
        else:
            if cur and (best is None or cur[1] - cur[0] > best[1] - best[0]):
                best = cur
            cur = None
    if cur and (best is None or cur[1] - cur[0] > best[1] - best[0]):
        best = cur
    if not best:
        return 0.0
    return best[1] + step


def loudness(path):
    """量响度(LUFS)。

    ⚠ ffmpeg 版本的输出格式不同:
      老版 "I:         -16.2 LUFS"
      新版 "Input Integrated:    -16.2 LUFS"
      两种都要认, 否则会返回 '?' —— 看起来像没测, 实际是格式没匹配上。
    """
    r = subprocess.run([FF, "-hide_banner", "-i", str(path), "-af",
                        "loudnorm=print_format=summary", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Input Integrated:" in line and "LUFS" in line:
            return line.strip().replace("Input Integrated:", "I:").strip()
        if line.strip().startswith("I:") and "LUFS" in line:
            return line.strip()
    return "?"


def clean_old(out):
    """清掉上一版产物 —— 避免下游误用陈旧文件。"""
    for pat in ("v*.mp4", "aa*.wav", "vert*.mp4", "final*.mp4", "silent.mp4",
                "card*.mp4", "intro.mp4", "outro.mp4", "_intro.png", "_outro.png",
                "_sub*.png", "_hl*.png", "_card*.png",
                "alist.txt", "list.txt", "vlist.txt", "valist.txt"):
        for f in out.glob(pat):
            f.unlink()


def push_filter(width, height, zoom, dx, dy, period):
    """推镜: 先整体放大 Z 倍, 再让裁剪窗口缓慢漂移。

    ⚠ 不用 zoompan(时长极易错), 用 scale + crop(x/y 带 t 表达式)。
    ⚠ crop 的 x/y 表达式在**放大后**的坐标系里 → 漂移量要除以 Z 折算。
    """
    z = float(zoom)
    W, H = width, height
    # 放大后尺寸
    sw, sh = int(W * z), int(H * z)
    # 裁剪窗口在放大图上的起点(居中) + 漂移
    x0 = (sw - W) / 2.0
    y0 = (sh - H) / 2.0
    amp_x = (sw - W) * 0.5
    amp_y = (sh - H) * 0.5
    if amp_x < 1:
        amp_x = 1.0
    if amp_y < 1:
        amp_y = 1.0
    x_expr = f"{x0:.2f}+{amp_x:.2f}*sin(t/{max(period,1):.2f})"
    y_expr = f"{y0:.2f}+{amp_y:.2f}*sin(t/{max(period*0.73,1):.2f}+1.1)"
    return (f"scale={sw}:{sh}:flags=lanczos,"
            f"crop={W}:{H}:x='{x_expr}':y='{y_expr}'")


def build_cards_and_highlights(cfg, plan, out, work):
    """为每段生成标题卡片段 + 高亮框 PNG。

    返回 (card_paths, highlight_map)
        card_paths[i]  = 该段前的标题卡 mp4 路径(可能 None)
        highlight_map[i] = [(png_path, start, end, box), ...]  段内相对时间
    """
    vcfg = cfg["video"]
    W, H = vcfg.get("width", 1920), vcfg.get("height", 1080)
    vcfg_v = cfg.get("visual", {})
    tc_cfg = vcfg_v.get("title_card", {})
    hl_cfg = vcfg_v.get("highlight", {})
    script_items = []
    sp = cfg.get("script_path")
    if sp:
        p = pathlib.Path(sp)
        if not p.is_absolute():
            p = pathlib.Path(cfg["_config_dir"]) / p
        if p.exists():
            script_items = json.loads(p.read_text())

    cards = [None] * len(plan)
    hls = [[] for _ in plan]

    card_dur = tc_cfg.get("duration", 1.8)
    card_fade = tc_cfg.get("fade", 0.28)

    # 🚨 **推镜安全区**: 推镜 = 放大 Z 倍后裁一个 WxH 的窗口, 窗口在
    #    [0, H*(Z-1)] 范围内漂移 → 原图坐标里, 只有
    #        y ∈ [H*(Z-1)/Z, H/Z] 这一带**任何时刻都在画面内**。
    #    边距 = H*(Z-1)/Z (不是 H*(Z-1)/2 —— 那是放大图上的像素, 换算回原图要除以 Z)。
    #    实测: 顶栏数字 y≈97, 标签牌放在框上方 y≈46 → 落在安全区外 →
    #    推镜一漂就被裁掉 → 观众看到一个没有标签的框。
    push_cfg = vcfg.get("push", {})
    Z = float(push_cfg.get("zoom", 1.06))
    mx = int(W * (Z - 1) / Z) + 4
    my = int(H * (Z - 1) / Z) + 4
    safe = (mx, my, mx, my)
    if push_cfg.get("enabled", True):
        print(f"推镜安全区: {safe} (zoom={Z} → 边距 {mx}x{my}px)")

    for p in plan:
        i = p["idx"]
        item = script_items[i] if i < len(script_items) else {}
        seg = p["seg"]

        # ── 标题卡 ──
        if tc_cfg.get("enabled", True) and item.get("title"):
            # 底图 = 该段内容开始后 1s 的一帧(自己的画面 → 有延续感)
            bg = out / f"_card{i}.png"
            visual.extract_frame(seg, p["black_end"] + 1.0, bg)
            accent = item.get("accent") or visual.PALETTE.get(item.get("module", ""),
                                                            visual.DEFAULT_ACCENT)
            card_png = out / f"_card{i}_full.png"
            visual.render_title_card(
                str(bg), str(card_png),
                title=item.get("title", ""),
                sub=item.get("card_sub"),
                module=item.get("module"),
                accent=accent, width=W, height=H,
                title_size=tc_cfg.get("title_size", 120),
                keyword=item.get("keyword"))
            card_mp4 = out / f"card{i}.mp4"
            visual.make_card_clip(card_png, card_mp4, duration=card_dur,
                                   fade=card_fade, width=W, height=H)
            cards[i] = card_mp4
            bg.unlink(missing_ok=True)
            card_png.unlink(missing_ok=True)

        # ── 高亮框 ──
        for f in item.get("focus", []) or []:
            needle = f.get("match")
            if not needle:
                continue
            at = float(f.get("at", 1.0))
            durf = float(f.get("dur", 5.0))
            fr = out / f"_hl{i}_{at:.1f}.png"
            visual.extract_frame(seg, p["black_end"] + at, fr)
            box = visual.locate_text(str(fr), needle)
            if not box:
                # 🚨 找不到目标 → **报错退出**, 绝不静默跳过
                sys.exit(f"❌ 第{i}段 {at:.1f}s 处找不到文字 {needle!r} —— "
                         f"高亮框必须定位到, 不能静默跳过(否则又是一个"
                         f"'看着都对但其实没框'的交付物)")
            accent = f.get("accent") or visual.PALETTE.get(item.get("module", ""),
                                                            visual.DEFAULT_ACCENT)
            hp = out / f"_hl{i}_{at:.1f}_box.png"
            visual.render_highlight(str(hp), box, label=f.get("label"),
                                    accent=accent, width=W, height=H,
                                    stroke=hl_cfg.get("stroke", 4),
                                    pad=hl_cfg.get("pad", 9),
                                    safe=safe)
            hls[i].append((hp, at, at + durf, box))
            fr.unlink(missing_ok=True)
    return cards, hls


def build_endcards(cfg, out, W, H):
    """生成片头/片尾片段 → out/intro.mp4, out/outro.mp4。

    🚨 片头片尾**由 compose 自己生成**, 不依赖外部预生成的文件 ——
       外部预生成的问题是: 配置改了不会重生成, 归档目录里的成片和配置对不上。
    """
    import endcard
    ecfg = cfg.get("endcards", {})
    out.mkdir(parents=True, exist_ok=True)

    intro_p = out / "intro.mp4"
    if ecfg.get("intro", {}).get("enabled", True):
        ic = ecfg.get("intro", {})
        endcard.render_intro(out / "_intro.png",
                             ic.get("title", "示例项目"),
                             ic.get("sub", "从数据到界面"),
                             ic.get("badge", "全流程拆解"),
                             width=W, height=H)
        endcard.make_intro_clip(out / "_intro.png", intro_p,
                                duration=ic.get("duration", 4.2),
                                width=W, height=H)
        print(f"片头: {intro_p.name} {dur(intro_p):.2f}s")

    outro_p = out / "outro.mp4"
    if ecfg.get("outro", {}).get("enabled", True):
        oc = ecfg.get("outro", {})
        pts = oc.get("points") or []
        endcard.render_outro(out / "_outro.png", pts,
                             oc.get("next", ""), width=W, height=H)
        endcard.make_outro_clip(out / "_outro.png", outro_p,
                                duration=oc.get("duration", 4.6),
                                width=W, height=H)
        print(f"片尾: {outro_p.name} {dur(outro_p):.2f}s")
    return intro_p, outro_p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())
    cfg["_config_dir"] = str(pathlib.Path(a.config).resolve().parent)
    work = pathlib.Path(cfg["paths"]["work_dir"])
    raw = work / cfg["paths"].get("raw_dir", "raw")
    audio = work / cfg["paths"].get("audio_dir", "audio")
    out = work / cfg["paths"].get("out_dir", "out")
    out.mkdir(parents=True, exist_ok=True)

    segs = sorted(raw.glob("seg*.webm"))
    if not segs:
        sys.exit(f"❌ {raw} 里没有分段 webm —— 先跑 recorder.py")

    vcfg = cfg["video"]
    plan = []

    for i, s in enumerate(segs):
        wav = audio / f"seg{i:02d}.mp3"
        if not wav.exists():
            wav = audio / f"seg{i:02d}.wav"
        if not wav.exists():
            sys.exit(f"❌ 缺旁白 {wav.name} —— 先跑 tts.py")
        be = black_end_time(s, work / "_blk")
        d = dur(s)
        wd = dur(wav)
        avail = d - be
        content = max(0.1, min(avail - 0.6, wd + 0.9))
        plan.append(dict(idx=i, seg=s, wav=wav, black_end=be, seg_dur=d,
                         wav_dur=wd, content=content,
                         # 帧对齐后的实际时长(与产物文件一致)
                         exact=round(content * int(vcfg.get("fps", 25)))
                         / float(int(vcfg.get("fps", 25)))))
        print(f"[{i}] {s.name} 黑场结束={be:.2f}s 段长={d:.2f}s 旁白={wd:.2f}s → 取 {content:.2f}s")

    # ── 横屏 ──
    clean_old(out)

    # ── 视觉层 ──
    vcfg_v = cfg.get("visual", {})
    cards, hls = build_cards_and_highlights(cfg, plan, out, work)
    if any(cards):
        print(f"标题卡: {sum(1 for c in cards if c)} 张")
    if any(hls):
        print(f"高亮框: {sum(len(h) for h in hls)} 个")

    # ── 片头/片尾 ──
    build_endcards(cfg, out, vcfg.get("width", 1920), vcfg.get("height", 1080))

    # ── 字幕: 先算好每段的字幕(相对段内时间) ──
    # 🚨🚨 字幕必须在**拼片阶段**烧, 不能成片后再烧!
    #    成片后再烧 = 二次编码 → 整帧重压缩 → **顶栏小字被压糊**。
    #    实测: 无字幕版整帧放大能读出「−1,652.90」, 二次编码后读不出来了
    #    (顶栏像素 md5 都变了) → **验收能力被字幕毁掉**。
    #    一次编码 = 顶栏像素与无字幕版完全一致。
    seg_cues = {}          # idx -> [(t_start, t_end, [lines]), ...]  (相对段内)
    sub_cfg = cfg.get("subtitle", {})
    # 🚨🚨🚨 script_path 相对 **配置文件所在目录** 解析, 不是 CWD!
    #    踩过的坑(血泪): 归档目录叫 script4.json, 父目录叫 script.json。
    #    用 CWD 解析 → 换个目录跑就找不到 → 字幕**静默跳过, 不报错**,
    #    流程照跑成功, 顶栏像素还和无字幕版完全一致 → **所有验收全过**,
    #    交付物却根本没有字幕! 用户一句"视频没字幕啊"才发现。
    #    教训: 字幕开关必须 fail loud —— 找不到文案就退出, 绝不能静默降级。
    script_path = pathlib.Path(cfg.get("script_path", "script.json"))
    if not script_path.is_absolute():
        script_path = pathlib.Path(cfg["_config_dir"]) / script_path
    if sub_cfg.get("enabled", True):
        if not script_path.exists():
            sys.exit(f"❌ 字幕已开启但找不到文案 {script_path} —— "
                     f"字幕必须烧进成片, 不能静默跳过")
        script_items = json.loads(script_path.read_text())

        # 🚨🚨🚨 **优先用 ASR 对齐轴** (真实语音时间), 没有才退回按字数摊。
        #    按字数摊是**猜的**: TTS 语速不均匀, 每段偏 0.3~1.5s,
        #    累积到片尾能偏好几秒 → 观众看到「字幕比人声慢半拍」→ 极难看。
        align_path = cfg.get("align_path")
        align = None
        if align_path:
            ap_ = pathlib.Path(align_path)
            if not ap_.is_absolute():
                ap_ = pathlib.Path(cfg["_config_dir"]) / ap_
            if ap_.exists():
                align = json.loads(ap_.read_text())
                print(f"字幕时间轴: ASR 对齐 ({ap_.name})")
            else:
                print(f"⚠ 找不到对齐文件 {ap_} —— 退回按字数分配时间(会有漂移)")
        if align:
            seg_cues = subtitle.build_cues_from_align(
                align, script_items, plan, sub_cfg.get("max_chars", 20))
            # 折行 + 校验
            fixed = {}
            for idx, lst in seg_cues.items():
                fl = []
                for (t0, t1, txt) in lst:
                    t1 = max(t1, t0 + 0.3)
                    fl.append((t0, t1, subtitle.wrap_two_lines(
                        txt, sub_cfg.get("max_chars", 20))))
                fixed[idx] = fl
            seg_cues = fixed
        else:
            for p in plan:
                idx = p["idx"]
                item = script_items[idx] if idx < len(script_items) else {}
                sents = subtitle.split_sentences(
                    item.get("text", ""), sub_cfg.get("max_chars", 20), 2)
                if not sents:
                    continue
                # 🚨 字幕铺满**整段**(content), 不是只铺旁白时长(wav_dur)。
                seg_total = float(p.get("content") or p["wav_dur"])
                total = sum(len(s) for s in sents) or 1
                t = 0.0
                lst = []
                for si, s in enumerate(sents):
                    # ⚠ 变量名别叫 dur —— 会遮蔽模块级的 dur() 函数
                    if si == len(sents) - 1:
                        # 🚨 最后一条**吃到段尾** —— 前面按比例分完会留空隙
                        seg_d = seg_total - t
                    else:
                        seg_d = seg_total * (len(s) / total)
                    lst.append((t, t + seg_d, subtitle.wrap_two_lines(
                        s, sub_cfg.get("max_chars", 20))))
                    t += seg_d
                seg_cues[idx] = lst
        print(f"字幕: {sum(len(v) for v in seg_cues.values())} 条")

    # 推镜参数
    push_cfg = vcfg_v.get("push", {})
    push_on = push_cfg.get("enabled", True) and bool(plan)
    push_chain = ""
    if push_on:
        push_chain = push_filter(
            vcfg.get("width", 1920), vcfg.get("height", 1080),
            push_cfg.get("zoom", 1.06), push_cfg.get("dx", 18),
            push_cfg.get("dy", 10), push_cfg.get("period", 9.0))

    # 标题卡时长(每张都一样) —— 时间轴要用
    # 🚨 帧对齐: 实际卡片时长 = round(duration*fps)/fps, 不是 duration。
    #    时间轴必须用**实际值**, 否则标题卡区间的起止全偏。
    _fps = int(cfg["video"].get("fps", 25))
    _cd = float(vcfg_v.get("title_card", {}).get("duration", 1.9))
    card_dur_total = max(1, int(round(_cd * _fps))) / float(_fps)

    parts = []          # concat 顺序: 标题卡 → 段落 → 标题卡 → 段落 ...
    for p in plan:
        i = p["idx"]
        vp = out / f"v{p['idx']}.mp4"
        cmd = [FF, "-y", "-loglevel", "error",
               "-ss", f"{p['black_end']:.3f}", "-i", str(p["seg"]),
               "-i", str(p["wav"])]
        # ⚠ 这里**不能**加 "-t content": 放在 -i 前面它是**输入选项**,
        #    只会限制音频那路的读取, 视频那路不受限 → 输出多出 1.3s。
        #    正确的做法是把 -t 放到命令末尾(输出选项), 见下方。
        cues = seg_cues.get(p["idx"], [])
        pngs = []
        for ci, (_, _, lines) in enumerate(cues):
            pp = out / f"_sub{p['idx']}_{ci}.png"
            im = subtitle.render_png(
                lines, vcfg.get("width", 1920), vcfg.get("height", 1080),
                subtitle.pick_font(),
                sub_cfg.get("font_size", 44), sub_cfg.get("margin_v", 60))
            im.save(pp)
            pngs.append((pp, cues[ci][0], cues[ci][1]))
        for pp, _, _ in pngs:
            cmd += ["-i", str(pp)]
        # ── 高亮框 PNG(在推镜之前 overlay) ──
        hlist = hls[i]
        for hp, _, _, _ in hlist:
            cmd += ["-i", str(hp)]

        # ── 滤镜链 ──
        fc = []
        base = "0:v"
        # 🚨🚨🚨 **输入下标必须严格按 -i 的实际添加顺序算**:
        #    实际顺序 = 0=视频, 1=音频, 2..2+S-1=字幕PNG, 2+S..=高亮框PNG
        #    之前高亮框从下标 2 开始算 → **高亮框指向了字幕PNG, 字幕指向了高亮框**
        #    → 框没画出来 + 大段字幕丢失(覆盖率掉到 79%)。
        #    教训: 加 -i 的顺序 = 用滤镜引用的顺序, 两者必须严格对应。
        sub_base = 2
        hl_base = sub_base + len(pngs)
        # 高亮框: overlay 到**原始内容**上(推镜之前) → 框跟着内容一起被推
        prev = base
        for k, (hp, s, e, _box) in enumerate(hlist):
            lbl = f"h{k}"
            fc.append(f"[{prev}][{hl_base + k}:v]overlay=0:0:"
                      f"enable='between(t,{s:.3f},{e:.3f})'[{lbl}]")
            prev = lbl
        # 推镜
        if push_on:
            lbl = "pz"
            fc.append(f"[{prev}]{push_chain}[{lbl}]")
            prev = lbl
        # 字幕: overlay 在**推镜之后**(字幕是屏幕层, 不该跟着内容被推)
        for k, (pp, s, e) in enumerate(pngs):
            lbl = f"s{k}"
            fc.append(f"[{prev}][{sub_base + k}:v]overlay=0:0:"
                      f"enable='between(t,{s:.3f},{e:.3f})'[{lbl}]")
            prev = lbl

        cmd += ["-filter_complex", ";".join(fc) + ";[1:a]apad[apada]",
                "-map", f"[{prev}]"]
        # 🚨🚨 **流顺序必须是 视频在前、音频在后** —— concat demuxer 按顺序对应流。
        #    之前把 `-map 1:a:0` 写在 `-map [prev]` 前面 → 段落的流 0 是音频,
        #    标题卡的流 0 是视频 → concat 直接判定格式不匹配 → exit 69。
        #    (concat 不会重排流, 只按 index 拼)
        cmd += ["-map", "[apada]"]
        # 🚨 有字幕时**只能 map 滤镜输出** —— 同时 -map 0:v:0 会产出**两条视频流**
        #    (原片 + 字幕片), 播放器默认放第一条 = 无字幕那条 → 白烧。
        # 🚨 音频必须**转成立体声** —— TTS 出的 mp3 是单声道, 标题卡是立体声,
        #    声道数不一致 → concat 混流失败。
        cmd += ["-c:v", "libx264", "-preset", vcfg.get("preset", "medium"),
                "-crf", str(vcfg.get("crf", 20)),
                "-pix_fmt", "yuv420p", "-r", str(vcfg.get("fps", 25)),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        # 🚨🚨 **`-t` 必须是输出选项(放在末尾), 不能放在 `-i` 前面**:
        #    放在 `-i` 前 = **输入选项** → 只限制**音频**输入的读取时长,
        #    视频那路完全不受限 → 实际输出比 content 长 1.3s
        #    (实测 v0 期望 19.33s, 实际 20.64s)。
        #    后果: 段尾多出来 1.3s **没有字幕** → 字幕覆盖率掉到 74%,
        #    而且**所有时间轴都错位**(标题卡/高亮框/字幕全偏)。
        #    → `-t` 放末尾 + `[1:a]apad` 补静音 → 输出精确等于 content。
        # 🚨🚨 **还要帧数取整**: content=19.332s × 25fps = 483.3 帧
        #    → 容器写 19.332 但实际 484 帧(=19.36s) → 又是半帧空洞
        #    → concat 后容器推断成 50fps(实际 25) → 播放器可能错帧。
        #    → frames = round(content*fps), 视频 -frames:v frames, 音频 -t frames/fps。
        fps_v = int(vcfg.get("fps", 25))
        nfr = max(1, int(round(p["content"] * fps_v)))
        exact = nfr / float(fps_v)
        cmd += ["-frames:v", str(nfr), "-t", f"{exact:.3f}", str(vp)]
        run(cmd)
        parts.append(vp)
        print(f"  → {vp.name} {vp.stat().st_size//1024}KB")

    # 清理临时字幕/高亮图
    for f in out.glob("_sub*.png"):
        f.unlink()
    for f in out.glob("_hl*.png"):
        f.unlink()

    # ── concat: 片头 → 标题卡 → 段落 ... → 片尾 ──
    # 🚨 片头/片尾是**独立片段**, 必须在最前/最后。
    #    它们的时长要**帧对齐**(和标题卡同规矩), 否则 concat 留半帧空洞。
    concat_list = []
    ecfg = cfg.get("endcards", {})
    intro_mp4 = out / "intro.mp4"
    outro_mp4 = out / "outro.mp4"
    if ecfg.get("intro", {}).get("enabled") and intro_mp4.exists():
        concat_list.append(intro_mp4)
    for i, p in enumerate(plan):
        if cards[i]:
            concat_list.append(cards[i])
        concat_list.append(out / f"v{p['idx']}.mp4")
    if ecfg.get("outro", {}).get("enabled") and outro_mp4.exists():
        concat_list.append(outro_mp4)

    alist = out / "alist.txt"
    alist.write_text("".join(f"file '{p.resolve()}'\n" for p in concat_list))
    silent = out / "silent.mp4"
    # 🚨 concat demuxer 需要**绝对路径** —— 相对路径会报 "Error opening input file"
    # 🚨🚨 **音频必须重编码, 不能 -c copy**:
    #    标题卡的静音 AAC 与段落的旁白 AAC **extradata 不同**,
    #    concat 直接 copy 会产出流损坏的音频
    #    (报错 "Decode error rate 0.865 exceeds maximum 0.666667" → 整条链失败)。
    #    视频流参数一致(同分辨率/帧率/像素格式), 视频可以 copy。
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(alist.resolve()),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         str(silent.resolve())])

    ln = vcfg.get("loudnorm", "I=-16:TP=-1.5:LRA=11")
    # 配置里存的是 loudnorm 的**参数部分**, 拼接时要补上滤镜名
    if not ln.startswith("loudnorm="):
        ln = "loudnorm=" + ln
    final = out / "final.mp4"

    # ── 配乐 ──
    # 🚨🚨🚨 配乐必须**闪避旁白**(ducking), 否则音乐盖过人声 → 观众听不清解说。
    #    实现: sidechaincompress —— 旁白当触发源, 音乐被压低。
    #    原理: 旁白电平高时压缩器压低配乐增益, 旁白停顿时配乐自动抬回来。
    #    ⚠ 参数: threshold=0.02(旁白超过 -34dB 触发), ratio=12(压得够狠),
    #      attack=8ms(快, 别让第一个字被盖), release=350ms(回落自然)。
    mcfg = cfg.get("music", {})
    music_path = mcfg.get("path")
    if music_path and pathlib.Path(music_path).exists():
        mpath = pathlib.Path(music_path).resolve()
        mdb = mcfg.get("gain_db", -19.0)
        # 🚨🚨🚨 **配乐起始偏移** —— 必须用!
        #    实测 594 号曲目: 0~6s 是**静音淡入**(mean -90dB),
        #    配乐从 0 开始 → 片头(0~4.2s)落在静音区 → 实测片头响度 -63dB,
        #    观众听不到任何配乐 → "加了配乐但等于没加"。
        #    → 从曲目的**响亮段**开始(offset), 让全片一上来就有能量。
        moff = float(mcfg.get("start_offset", 0.0))
        total_dur = dur(silent)
        # 🚨🚨🚨 **必须用单个 atrim(start+end), 不能串联两个 atrim**:
        #    实测 `atrim=start=8,aloop=...,atrim=0:210.13` 只输出 202.13s,
        #    末尾 8 秒直接是空的 → 片尾配乐断掉(实测 -41dB)。
        #    原因: 第二个 atrim 的 end 是相对**原始输入流**算的, 不是相对
        #    前一个 atrim 之后的流 → 两次裁剪叠加, 长度错乱。
        #    正确: `atrim=start=OFFSET:end=OFFSET+总长` 一个搞定;
        #    循环交给输入选项 `-stream_loop -1`(比 aloop 滤镜可靠)。
        af = (f"[1:a]atrim=start={moff:.3f}:end={moff + total_dur:.3f},"
              f"asetpts=N/SR/TB,volume={mdb}dB,afade=t=in:st=0:d=1.2,"
              f"afade=t=out:st={max(0.0, total_dur-2.2):.3f}:d=2.2[bg];"
              f"[0:a]asplit=2[va][sc];"
              f"[bg][sc]sidechaincompress=threshold=0.02:ratio=12:"
              f"attack=8:release=350:makeup=1[bgd];"
              f"[va][bgd]amix=inputs=2:duration=first:dropout_transition=0:"
              f"weights=1 1,"
              # 🚨🚨🚨 **混完必须再做 loudnorm** —— 只用 alimiter 的话
              #    实测响度只有 -35.3 LUFS(目标 -16) → 观众要手动拉音量。
              #    顺序: 混音 → 限幅 → 归一化 → 输出。
              f"alimiter=limit=0.95,{ln},aresample=44100[a]")
        run([FF, "-y", "-loglevel", "error",
             "-i", str(silent.resolve()),
             "-stream_loop", "-1", "-i", str(mpath),
             "-filter_complex", af, "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
             "-movflags", "+faststart", str(final.resolve())])
        print(f"配乐: {mpath.name} (增益 {mdb}dB, 已闪避旁白)")
    else:
        # 🚨 滤镜链必须用 -filter_complex —— 用 -af 时输出标签不存在,
        #    报错 "Output with label 'a' does not exist in any defined filter graph"
        run([FF, "-y", "-loglevel", "error", "-i", str(silent.resolve()),
             "-filter_complex", f"[0:a]{ln},aresample=44100,apad=pad_dur=0.5[a]",
             "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
             "-movflags", "+faststart", str(final.resolve())])
    print(f"\n横屏: {final} {final.stat().st_size//1024//1024}MB {dur(final):.2f}s")
    print(f"响度: {loudness(final)}")

    (out / "compose_plan.json").write_text(json.dumps(
        [{k: (str(v) if isinstance(v, pathlib.PurePath) else v) for k, v in p.items()}
         for p in plan], ensure_ascii=False, indent=1))

    # ── 时间轴: 告诉验收哪些区间**本来就没有字幕**(标题卡) ──
    # 🚨 标题卡是静音的章节卡, **故意没有字幕**。
    #    验收脚本如果按"全片每帧都该有字幕"来算 → 覆盖率必然掉到 75%,
    #    然后**误报"成片没有字幕"** → 白折腾一轮排查。
    #    → 把标题卡区间写进 timeline.json, 验收时排除。
    tl = []
    tt = 0.0
    # 🚨 片头也要写进时间轴 —— 验收脚本按"全片每帧都该有字幕"算的话,
    #    片头(无字幕)会被误判成"成片没字幕"。
    if ecfg.get("intro", {}).get("enabled") and intro_mp4.exists():
        d_intro = dur(intro_mp4)
        tl.append({"kind": "intro", "start": round(tt, 3), "end": round(tt + d_intro, 3)})
        tt += d_intro
    for i, p in enumerate(plan):
        if cards[i]:
            tl.append({"kind": "title_card", "seg": i,
                       "start": round(tt, 3), "end": round(tt + card_dur_total, 3)})
            tt += card_dur_total
        tl.append({"kind": "segment", "seg": i,
                   "start": round(tt, 3), "end": round(tt + p["exact"], 3)})
        tt += p["exact"]
    if ecfg.get("outro", {}).get("enabled") and outro_mp4.exists():
        d_outro = dur(outro_mp4)
        tl.append({"kind": "outro", "start": round(tt, 3), "end": round(tt + d_outro, 3)})
        tt += d_outro
    (out / "timeline.json").write_text(json.dumps(
        {"total": round(tt, 3), "title_card_duration": card_dur_total,
         "tracks": tl}, ensure_ascii=False, indent=1))
    print(f"时间轴 → {out / 'timeline.json'} (总长 {tt:.2f}s)")

    if seg_cues:
        print(f"✅ 字幕已随拼片烧入({sum(len(v) for v in seg_cues.values())} 条) → {final}")
    if any(cards):
        print(f"✅ 标题卡 {sum(1 for c in cards if c)} 张已插入 → {final}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
