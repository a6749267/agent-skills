#!/usr/bin/env python3
r"""画面增强层 —— 标题压屏 / 高亮框 / 聚光 / 推镜。**节奏与画面质量的执行者**。

用法(独立测试):
    python visual.py --card --frame f.png --title "数据从哪来" --sub "数据模块" \
        --accent "#00e5ff" --out card.png

为什么需要这一层(实测数据, 别凭感觉):
    参考视频(技术开源项目 89s / ComfyUI 整合包 1210s) vs 我们上一版成片(187s):

      指标                        参考        我们      差距
      视觉变化强度(帧差均值)      19.35       0.87      22 倍
      变化帧/分钟                 54.6       14.1      3.9 倍
      长时间死画面                几乎没有   90%       ← 致命
      大标题压屏                  每段都有   0
      画面标注(框选)              有         0
      局部特写/半屏               有         0

    死画面的根因: 录屏是**静态界面**, 30 秒不动一个像素 → 观众划走。
    三个参考视频的共通解法(全部抄):
      1. **大标题压屏** —— 模糊底 + 超大粗衬线标题 + 荧光色关键词,
         每个章节切一次 → 制造真实的硬切点, 同时给观众路标。
      2. **画面标注** —— 青绿圆角框圈住关键数字/图表区, 观众知道看哪。
      3. **轻微推镜** —— 每帧都在动, 死画面归零。

🚨 标题卡必须**先无声播 2.2s 再进旁白**, 不要把标题压在旁白上:
   压上去 → 观众同一时刻要读标题 + 听旁白 + 看界面 → 三重负担, 字幕区还被占。

🚨 标题卡用**该段自己的画面**做底(不是纯色): 观众能看出这是刚才那屏的延续,
   不会有"突然黑屏"的断裂感。

🚨 关键词用**荧光色**(青/玫红/亮蓝), 其余白色 —— 颜色即层级,
   不要整句都上色, 会糊。

依赖: Pillow + ffmpeg(+ 可选 macOS Vision 做高亮框自动定位)
"""
import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FF = "ffmpeg"
FP = "ffprobe"

# 章节配色 —— 一个模块一个颜色, 观众靠颜色定位, 不用听解说
PALETTE = {
    "工作台": "#00e5ff",
    "数据": "#3b82f6",
    "实验面板": "#ff2d55",
    "历史记录": "#f59e0b",
    "工具面板": "#a855f7",
    "执行台": "#22c55e",
    "结尾": "#f97316",
}
DEFAULT_ACCENT = "#00e5ff"

# 大标题用**粗衬线体**(Songti SC Black) —— 参考视频的主标题就是这个调性
TITLE_FONTS = [
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),   # Songti SC Black
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),   # Bold
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
]
LABEL_FONTS = [
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
]


def _font(candidates, size):
    from PIL import ImageFont
    for path, idx in candidates:
        if pathlib.Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()


def hex_rgb(h):
    h = (h or DEFAULT_ACCENT).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ── 标题卡 ───────────────────────────────────────────────────────
def render_title_card(frame_png, out_png, title, sub=None, module=None,
                      accent=DEFAULT_ACCENT, width=1920, height=1080,
                      blur=26, dim=0.55, title_size=120, sub_size=42,
                      keyword=None):
    """生成标题卡 PNG: 模糊底 + 超大粗衬线标题 + 荧光色关键词 + 下划线 + 副标题。

    ⚠ 底图用**该段自己的画面**做模糊 → 观众看得出延续性。

    ⚠ **关键词用荧光色, 其余白色** —— 参考视频的关键手法。
       颜色即层级: 整句都上色 → 糊; 只给核心词上色 → 眼睛自己会跳过去。
       keyword="从哪来" → 标题里这段染成 accent 色, 其余白色。

    ⚠ 副标题**不要重复模块名** —— 角标已经写了"数据", 副标题再写"数据模块…"
       → 同一信息出现两次 → 显啰嗦。角标管分类, 副标题管内容。

    排版间距(评审实测要求, 别乱改):
        角标→主标题  40~48px
        主标题→下划线 14~18px
        下划线→副标题 28~34px
        整组比视觉中心低 3~5%
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
    base = Image.open(frame_png).convert("RGB").resize((width, height), Image.LANCZOS)
    base = base.filter(ImageFilter.GaussianBlur(blur))
    # 压暗 → 白字才跳出来
    base = ImageEnhance.Brightness(base).enhance(1.0 - dim)
    base = ImageEnhance.Color(base).enhance(0.55)

    im = base.convert("RGBA")
    rgb = hex_rgb(accent)

    tf = _font(TITLE_FONTS, title_size)
    lf = _font(LABEL_FONTS, sub_size)

    # 竖向渐变遮罩(下暗上暗, 中间亮) → 文字区永远有对比
    grad = Image.new("L", (1, height), 0)
    gd = ImageDraw.Draw(grad)
    for y in range(height):
        t = abs(y - height * 0.44) / (height * 0.62)
        gd.point((0, y), fill=int(max(0, min(190, (t ** 1.7) * 210))))
    grad = grad.resize((width, height))
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade.putalpha(grad)
    im = Image.alpha_composite(im, shade)
    d = ImageDraw.Draw(im)

    # ── 标题分段(关键词染色) ──
    parts = []
    if keyword and keyword in title:
        i = title.index(keyword)
        if i > 0:
            parts.append((title[:i], (255, 255, 255, 255)))
        parts.append((keyword, rgb + (255,)))
        if i + len(keyword) < len(title):
            parts.append((title[i + len(keyword):], (255, 255, 255, 255)))
    else:
        parts.append((title, (255, 255, 255, 255)))

    widths = []
    for txt, _ in parts:
        bb = d.textbbox((0, 0), txt, font=tf)
        widths.append(bb[2] - bb[0])
    tw = sum(widths)
    bb0 = d.textbbox((0, 0), parts[0][0], font=tf)
    th = bb0[3] - bb0[1]

    group_center = height * 0.47          # 整组比视觉中心低 3~5%
    badge_h = 52 if module else 0
    gap_badge = 44 if module else 0
    title_top = int(group_center - th / 2) - (badge_h + gap_badge) // 2
    if module:
        badge_cy = title_top - gap_badge - badge_h // 2
    ul_y = title_top + th + 18
    sub_y = ul_y + 42

    # 模块角标(小一点, 别跟主标题抢注意力)
    if module:
        tag = f" {module} "
        tb = d.textbbox((0, 0), tag, font=lf)
        twd = tb[2] - tb[0]
        d.rounded_rectangle([width // 2 - twd // 2 - 22, badge_cy - 26,
                             width // 2 + twd // 2 + 22, badge_cy + 24],
                            radius=24, fill=rgb + (235,))
        d.text((width // 2 - twd // 2, badge_cy - 24), tag, font=lf,
               fill=(12, 14, 18, 255))

    # 主标题(逐段绘制, 关键词染色)
    cx = (width - tw) // 2
    stroke = max(3, title_size // 20)
    for (txt, color), wseg in zip(parts, widths):
        bb = d.textbbox((0, 0), txt, font=tf)
        ty = title_top - bb[1]
        d.text((cx, ty), txt, font=tf, fill=color,
               stroke_width=stroke, stroke_fill=(8, 10, 14, 255))
        cx += wseg

    # 荧光色下划线 —— 承托整行标题(约 58% 宽), 不要短到像装饰
    ul_w = int(tw * 0.58)
    d.rounded_rectangle([width // 2 - ul_w // 2, ul_y, width // 2 + ul_w // 2, ul_y + 11],
                        radius=5, fill=rgb + (255,))

    # 副标题(提亮 ~10%, 加粗)
    if sub:
        sb = d.textbbox((0, 0), sub, font=lf)
        sw = sb[2] - sb[0]
        d.text(((width - sw) // 2, sub_y), sub, font=lf, fill=(242, 246, 252, 255),
               stroke_width=2, stroke_fill=(8, 10, 14, 220))

    im.convert("RGB").save(out_png, quality=94)
    return out_png


def make_card_clip(card_png, out_mp4, duration=2.2, fade=0.3,
                   width=1920, height=1080, drift=True, fps=25):
    """标题卡 → 短片(带淡入淡出 + 轻微漂移)。**无声**。

    🚨 不要给标题卡配旁白 —— 标题卡是路标, 旁白在它之后开始讲。

    🚨🚨 **帧数必须取整, 且容器时长 = 帧数/fps**:
       duration=1.9s × 25fps = 47.5 帧 → 实际存 47 帧, 但容器写 1.9s
       → concat demuxer 按**容器时长**算下一段的起始偏移
       → 每张卡后面留一个**半帧空洞** → PTS 出现 0.08s 跳变
       → 容器推断出 r_frame_rate=50/1(实际 25fps) → 部分播放器/剪辑软件会错帧。
       (实测: 8 张卡 → 8 个空洞 → 前几帧 PTS 间隔 0.04/0.04/0.08/0.08)
       → 正确做法: frames = round(duration*fps), 视频用 -frames:v frames,
         音频用 -t frames/fps, 两者严格相等。
    """
    frames = max(1, int(round(duration * fps)))
    exact = frames / float(fps)
    cmd = [FF, "-y", "-loglevel", "error",
           "-loop", "1", "-i", str(card_png)]
    if drift:
        # 缓慢推近 + 轻微平移 → 标题卡本身也不是死画面
        vf = (f"scale={int(width*1.14)}:{int(height*1.14)}:flags=lanczos,"
              f"zoompan=z='min(zoom+0.0006,1.09)':x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps},"
              f"fade=t=in:st=0:d={fade},fade=t=out:st={exact-fade:.3f}:d={fade},"
              f"format=yuv420p")
    else:
        vf = (f"scale={width}:{height}:flags=lanczos,"
              f"fade=t=in:st=0:d={fade},fade=t=out:st={exact-fade:.3f}:d={fade},"
              f"format=yuv420p")
    # 配一路静音 → concat 时所有分段都有音轨(否则 concat demuxer 报错)
    # 🚨 音频参数必须和**段落视频完全一致**(采样率/声道/码率),
    #    否则 concat demuxer 混流时 AAC 解码报错
    #    ("Decode error rate 0.865 exceeds maximum 0.666667") → 整条链失败。
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", vf, "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-frames:v", str(frames), "-r", str(fps),
            "-t", f"{exact:.3f}", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return out_mp4


# ── 高亮框 ───────────────────────────────────────────────────────
def locate_text(frame_png, needle, exact=False, scale=2.0):
    """用 macOS Vision 在帧里找文字 → 返回**原始 1920x1080 坐标**系的像素 bbox。

    ⚠🚨 **必须先放大再 OCR** —— 这是踩过的坑:
       macOS Vision 对**小字**在原生分辨率下会读成乱码。
       实测同一帧:
         1920x1080 原生 → 顶栏「总权益 1,020,518.26」读成乱码, **定位不到**
         3840x2160 放大 → 正常读出, bbox 精确
       所以: 放大 2× 做 OCR, 拿到的 bbox 再**除回 scale** 换算成原图坐标。

    Vision 的 bbox 原点在**左下角**(y 向上) → 必须翻转。
    找不到返回 None(调用方要 fail loud, 不能静默跳过)。
    """
    try:
        import objc
        from Vision import VNRecognizeTextRequest, VNImageRequestHandler
        from Quartz import CIImage
        from Foundation import NSURL
    except Exception:
        return None
    from PIL import Image
    src = Image.open(frame_png).convert("RGB")
    W, H = src.size
    if scale and scale != 1.0:
        big = src.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
        tmp = pathlib.Path(frame_png).with_suffix(".ocr.png")
        big.save(tmp)
        ocr_path = str(tmp)
    else:
        ocr_path = str(frame_png)
    url = NSURL.fileURLWithPath_(str(pathlib.Path(ocr_path).resolve()))
    ci = CIImage.imageWithContentsOfURL_(url)
    if ci is None:
        return None
    handler = VNImageRequestHandler.alloc().initWithCIImage_options_(ci, None)
    req = VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    ok, _ = handler.performRequests_error_([req], None)
    if scale and scale != 1.0:
        pathlib.Path(ocr_path).unlink(missing_ok=True)
    if not ok:
        return None
    OW, OH = int(W * (scale or 1)), int(H * (scale or 1))
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if not cand or not len(cand):
            continue
        s = str(cand[0].string())
        hit = (s == needle) if exact else (needle in s)
        if not hit:
            continue
        bb = obs.boundingBox()
        x = bb.origin.x * OW
        y = (1.0 - bb.origin.y - bb.size.height) * OH
        k = 1.0 / (scale or 1.0)
        return (int(x * k), int(y * k),
                int(bb.size.width * OW * k), int(bb.size.height * OH * k))
    return None


def render_highlight(out_png, box, label=None, accent=DEFAULT_ACCENT,
                     width=1920, height=1080, stroke=4, pad=9,
                     label_size=32, label_gap=14, safe=(0, 0, 0, 0)):
    """高亮框 PNG(整帧透明): 圆角描边 + 可选标签牌。

    box = (x, y, w, h) 像素。
    safe = (left, top, right, bottom) —— **推镜安全边距**。

    🚨🚨 **标签牌位置必须避开推镜安全区**:
       推镜会把内容放大 Z 倍再裁窗口漂移 → 画面边缘的内容会被裁掉。
       顶栏数字(y≈97)的框, 标签牌默认放在框**上方** → 落在安全区外
       → 被裁掉 → 观众只看到一个没有标签的框。
       (实测: 框顶部被切到 y=0, 标签牌整个不见)
       → 标签牌按 **上→下→右→左** 顺序挑第一个**完整落在安全区里**的位置。

    ⚠ **框要贴着目标, 别太空** —— 框大一圈 → 观众以为框里所有东西都重要。
    ⚠ 四角加粗**别太重**(stroke+2 即可) —— 装饰过重 → 像儿童贴纸, 不专业。
    """
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    rgb = hex_rgb(accent)
    x, y, w, h = box
    r = (x - pad, y - pad, x + w + pad, y + h + pad)
    d.rounded_rectangle(r, radius=8, outline=rgb + (255,), width=stroke)
    # 四角轻微加粗 → 像"标注"而不是"选中框"
    L = 26
    for cx, cy, dx, dy in ((r[0], r[1], 1, 1), (r[2], r[1], -1, 1),
                           (r[0], r[3], 1, -1), (r[2], r[3], -1, -1)):
        d.line([(cx, cy), (cx + dx * L, cy)], fill=rgb + (255,), width=stroke + 2)
        d.line([(cx, cy), (cx, cy + dy * L)], fill=rgb + (255,), width=stroke + 2)

    if label:
        f = _font(LABEL_FONTS, label_size)
        tb = d.textbbox((0, 0), label, font=f)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        sl, st, sr, sb = safe
        chip_h = th + 16
        chip_w = tw + 22
        # 候选位置: 上 → 下 → 右 → 左
        cands = [
            ("up",   (r[0], r[1] - label_gap - chip_h), None),
            ("down", (r[0], r[3] + label_gap), None),
            ("right", (r[2] + label_gap, r[1]), None),
            ("left", (r[0] - label_gap - chip_w, r[1]), None),
        ]
        placed = None
        for _name, (lx, ly), _ in cands:
            if (lx >= sl and ly >= st and
                    lx + chip_w <= width - sr and ly + chip_h <= height - sb):
                placed = (lx, ly)
                break
        if placed is None:
            # 都不行 → 用最不越界的那个
            placed = (max(sl, min(r[0], width - sr - chip_w)),
                      max(st, min(r[1] - label_gap - chip_h, height - sb - chip_h)))
        lx, ly = placed
        d.rounded_rectangle([lx, ly, lx + chip_w, ly + chip_h],
                            radius=7, fill=rgb + (240,))
        d.text((lx + 11, ly + 8 - tb[1]), label, font=f, fill=(10, 12, 16, 255))
    im.save(out_png)
    return out_png


def render_spotlight(out_png, box, width=1920, height=1080, strength=120,
                     accent=DEFAULT_ACCENT):
    """聚光 PNG: 焦点区留亮, 其余压暗 → 观众视线被压到该看的地方。"""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new("RGBA", (width, height), (0, 0, 0, strength))
    x, y, w, h = box
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=(0, 0, 0, 0))
    im = im.filter(ImageFilter.GaussianBlur(6))
    d2 = ImageDraw.Draw(im)
    rgb = hex_rgb(accent)
    d2.rounded_rectangle([x, y, x + w, y + h], radius=12,
                         outline=rgb + (150,), width=3)
    im.save(out_png)
    return out_png


# ── 推镜 ─────────────────────────────────────────────────────────
def kenburns_filter(width=1920, height=1080, zoom=1.05, step=0.0005, fps=25):
    """轻微推镜滤镜串 —— 每一帧都在动, 死画面归零。

    ⚠ zoompan 的 d=1(每输入帧出 1 帧)才不会把时长搞乱;
      d 设成大数会按 zoompan 的 d 重新计帧 → 时长错乱。
    ⚠ 先 scale 放大再 zoompan, 否则放大的还是原分辨率 → 糊。
    """
    return (f"scale={int(width*1.35)}:{int(height*1.35)}:flags=lanczos,"
            f"zoompan=z='min(zoom+{step},{zoom})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={width}x{height}:fps={fps}")


def extract_frame(video, t, out_png, scale=None):
    vf = []
    if scale:
        vf.append(f"scale={scale[0]}:{scale[1]}:flags=lanczos")
    cmd = [FF, "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
           "-frames:v", "1"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd.append(str(out_png))
    subprocess.run(cmd, check=True)
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", action="store_true", help="生成标题卡")
    ap.add_argument("--frame", help="底图(抽帧 PNG)")
    ap.add_argument("--title")
    ap.add_argument("--sub")
    ap.add_argument("--module")
    ap.add_argument("--accent")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.card:
        accent = a.accent or PALETTE.get(a.module or "", DEFAULT_ACCENT)
        render_title_card(a.frame, a.out, a.title or "", a.sub, a.module, accent=accent)
        print(f"标题卡 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
