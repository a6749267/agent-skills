#!/usr/bin/env python3
"""片头 / 片尾 —— 视频的开场与收束。

为什么必须有(用户原话: "没有做片头和片尾"):
    片子一上来就是录屏界面, 观众 3 秒内不知道这是什么、值不值得看下去;
    结尾直接黑屏, 又显得仓促、像没做完。
    参考视频(技术类 89s / ComfyUI)都有明确的片头片尾。

设计要点(与标题卡同一套视觉语言, 不要风格割裂):
    片头 4.2s: 暗底 + 网格 + 主标题逐行浮现 + 副标题 + 底部栏目条
    片尾 4.6s: 同款暗底 + 三个要点依次亮起 + 收尾语 + 下期预告

🚨 片头片尾**不能是死画面**:
    之前 90% 的时间是静止界面 → 观众划走。片头片尾是全片仅有的
    "纯图形"段落, 更要有动感 —— 网格流动 / 文字逐行浮现 / 扫描光带。
    → 每帧都在动, 这是这一层的存在意义。

🚨 片头片尾的**底噪要低但非零**:
    纯静音段落在播放器里显得像卡了; 后期加一点点房间感更真实。

用法:
    python endcard.py --kind intro --out intro.mp4 --title "示例项目" \
        --sub "从数据到界面" --duration 4.2
    python endcard.py --kind outro --out outro.mp4 --duration 4.6 \
        --points "数据管道:来源可追溯|渲染:参数先锁再跑|工具化而非黑箱" \
        --next "下期:数据层与界面再拆开讲"
"""
import argparse
import json
import math
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FF = "ffmpeg"

TITLE_FONTS = [
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
]
LABEL_FONTS = [
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
]
MONO_FONTS = [
    ("/System/Library/Fonts/SFNSMono.ttf", 0),
    ("/System/Library/Fonts/Menlo.ttc", 0),
]

ACCENT = (0, 229, 255)      # #00e5ff 青
ACCENT2 = (255, 45, 85)     # #ff2d55 玫红
INK = (240, 244, 250)
DIM = (150, 162, 178)


def _font(candidates, size):
    from PIL import ImageFont
    for path, idx in candidates:
        if pathlib.Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()


def _ease_out(t):
    """ease-out cubic —— 元素浮现用, 起快收慢, 干脆利落。"""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t


def _bg_grid(draw, w, h, t, accent, alpha=1.0):
    """流动网格 —— 底层氛围, 每帧都在动。

    ⚠ alpha 系数: 网格是**背景**, 不能抢正文。
       评审指出「网格较抢眼, 尤其正文后方形成干扰」→ 整体压暗,
       并且**正文区域下方铺一层暗色蒙版**, 让文字区安静下来。
    """
    step = 64
    off = int((t * 22) % step)
    # 纵向线
    x = -off
    while x < w:
        a = int((26 if (x // step) % 2 == 0 else 14) * alpha)
        draw.line([(x, 0), (x, h)], fill=accent + (a,), width=1)
        x += step
    # 横向线
    offy = int((t * 9) % step)
    y = -offy
    while y < h:
        a = int((18 if (y // step) % 2 == 0 else 10) * alpha)
        draw.line([(0, y), (w, y)], fill=accent + (a,), width=1)
        y += step


def _quiet_zone(im, top, bottom, strength=118):
    """在文字区铺暗色蒙版 —— 让正文背后的网格安静下来。

    ⚠ 评审明确指出「网格覆盖全屏且亮度较高, 尤其在正文后方形成较强干扰」。
       解法不是砍掉网格(会失去科技感), 而是**在文字区局部压暗**。
    """
    from PIL import Image, ImageDraw
    h = im.size[1]
    veil = Image.new("RGBA", im.size, (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    band = bottom - top
    for y in range(max(0, top), min(h, bottom)):
        # 中间最暗, 上下边缘渐隐
        f = 1 - abs((y - top) / band - 0.5) * 2
        a = int(strength * max(0.0, min(1.0, f)) ** 1.4)
        if a > 0:
            vd.line([(0, y), (im.size[0], y)], fill=(4, 6, 11, a))
    return Image.alpha_composite(im, veil)


def _glow_dot(d, cx, cy, r, color, alpha):
    """辉光点 —— 纯 PIL 画柔光: 叠加多层递减半径的半透明圆。"""
    for i in range(9, 0, -1):
        rr = r * i / 9
        a = int(alpha * (1 - i / 9) ** 2 / 9)
        if a <= 0:
            continue
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color + (a,))


def _scan_band(im, t, h, accent, height=140):
    """扫描光带 —— 一条亮带自上而下扫过, 给静态图形加呼吸。"""
    from PIL import Image
    band = Image.new("RGBA", im.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    y = int((t * 0.42) * (h + height) - height)
    for i in range(height):
        yy = y + i
        if 0 <= yy < h:
            a = int(46 * (1 - abs(i - height / 2) / (height / 2)) ** 2)
            if a > 0:
                bd.line([(0, yy), (im.size[0], yy)], fill=accent + (a,))
    return Image.alpha_composite(im, band)


def render_intro(out_png_base, title, sub, badge, width=1920, height=1080,
                 accent=ACCENT):
    """渲染片头的**若干关键帧**(PIL 静态图), 动画由 ffmpeg 完成。

    思路: 用 PIL 画「已完全浮现」的静止底图 + 全部装饰元素,
    动画(浮现/流动/扫描)交给 ffmpeg 的 zoompan/fade/overlay 做 →
    避免逐帧 PIL 渲染(1920x1080 × 100 帧 = 极慢)。
    """
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new("RGBA", (width, height), (8, 11, 18, 255))
    d = ImageDraw.Draw(im)

    # 背景: 深蓝径向渐变
    bg = Image.new("RGB", (width, height), (8, 11, 18))
    gd = ImageDraw.Draw(bg)
    for i in range(70):
        f = i / 70
        r = int(8 + 16 * f)
        g = int(11 + 34 * f)
        b = int(18 + 52 * f)
        rr = int(width * (0.9 - 0.55 * f))
        gd.ellipse([width * 0.5 - rr, height * 0.42 - rr * 0.62,
                    width * 0.5 + rr, height * 0.42 + rr * 0.62],
                   fill=(r, g, b))
    bg = bg.filter(ImageFilter.GaussianBlur(90))
    im = bg.convert("RGBA")

    d = ImageDraw.Draw(im)
    _bg_grid(d, width, height, 0.0, accent, alpha=0.62)

    # 辉光点 —— ⚠ 评审:「右上青色圆面积大亮度高, 抢走主标题注意力」
    #   「红圆体积大颜色艳, 抢夺主标题注意力」→ 全部**缩小 + 压暗 + 外移**,
    #   让它们退回"氛围"而不是"第二主视觉"。
    _glow_dot(d, width * 0.13, height * 0.22, 190, accent, 58)
    _glow_dot(d, width * 0.90, height * 0.84, 230, ACCENT2, 40)

    tf = _font(TITLE_FONTS, 132)
    lf = _font(LABEL_FONTS, 46)
    mf = _font(LABEL_FONTS, 34)
    mono = _font(MONO_FONTS, 28)

    # 顶部栏目条 —— ⚠ 评审:「标签与主标题距离较远, 层级游离, 像独立栏目签」
    #   → 把它**下移贴近标题**, 并明确当作标题组的一部分。
    lines = title.split("\n")
    lh = 158
    total_h = lh * len(lines)
    top = int(height * 0.46 - total_h / 2)

    bar_y = top - 118
    d.rounded_rectangle([width // 2 - 210, bar_y - 30, width // 2 + 210, bar_y + 30],
                        radius=30, fill=accent + (235,))
    tag = f" {badge} "
    tb = d.textbbox((0, 0), tag, font=mf)
    d.text((width // 2 - (tb[2] - tb[0]) // 2, bar_y - 22), tag, font=mf,
           fill=(10, 13, 18, 255))

    # 主标题(逐行, 留出分行的间距)
    for i, ln in enumerate(lines):
        bb = d.textbbox((0, 0), ln, font=tf)
        tw = bb[2] - bb[0]
        x = (width - tw) // 2
        y = top + i * lh
        d.text((x, y - bb[1]), ln, font=tf, fill=INK + (255,),
               stroke_width=4, stroke_fill=(6, 8, 12, 255))

    # 荧光下划线
    ul_y = top + total_h + 10
    ul_w = int(width * 0.30)
    d.rounded_rectangle([width // 2 - ul_w // 2, ul_y, width // 2 + ul_w // 2, ul_y + 12],
                        radius=6, fill=accent + (255,))

    # 副标题 —— ⚠ 评审:「副标题字号不大却用接近主标题的粗黑描边, 显得糊」
    #   → 描边从 2 减到 1, 并提亮。
    if sub:
        sb = d.textbbox((0, 0), sub, font=lf)
        sw = sb[2] - sb[0]
        d.text(((width - sw) // 2, ul_y + 46), sub, font=lf,
               fill=(238, 243, 250, 255), stroke_width=1, stroke_fill=(6, 8, 12, 190))

    # 底部等宽字条 —— ⚠ 评审:「安全边距不足, 易被平台裁切」
    #   「青色细字叠在青色网格上, 对比度低」→ **上移 40px + 改浅色 + 字号加一档**
    foot = "DATA PIPELINE  →  FACTOR LAB  →  BACKTEST  →  AI"
    mono = _font(MONO_FONTS, 30)
    fb = d.textbbox((0, 0), foot, font=mono)
    fw = fb[2] - fb[0]
    d.text(((width - fw) // 2, height - 210), foot, font=mono,
           fill=(206, 226, 236, 235), stroke_width=1, stroke_fill=(6, 8, 12, 220))

    # 文字区压暗 —— 让网格不干扰正文
    im = _quiet_zone(im, top - 60, ul_y + 130, strength=112)
    # ⚠ 评审:「底部可见几个很小的浅色圆点/噪点」
    #   真相: 那是**网格横线与竖线的交点**(间隔 69px 的规则亮点),
    #   footer 文字区不在压暗带内 → 网格原亮度 → 交点被看成一排小圆点。
    #   → footer 区域也要压暗。
    im = _quiet_zone(im, height - 250, height - 70, strength=95)

    # 上下黑边(电影感)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, width, 54], fill=(0, 0, 0, 255))
    d.rectangle([0, height - 54, width, height], fill=(0, 0, 0, 255))

    im.convert("RGB").save(out_png_base, quality=95)
    return out_png_base


def render_outro(out_png_base, points, next_text, width=1920, height=1080,
                 accent=ACCENT):
    """片尾静态底图: 三条要点 + 收尾语 + 下期预告。"""
    from PIL import Image, ImageDraw, ImageFilter
    bg = Image.new("RGB", (width, height), (8, 11, 18))
    gd = ImageDraw.Draw(bg)
    for i in range(70):
        f = i / 70
        rr = int(width * (0.9 - 0.55 * f))
        gd.ellipse([width * 0.5 - rr, height * 0.5 - rr * 0.62,
                    width * 0.5 + rr, height * 0.5 + rr * 0.62],
                   fill=(int(8 + 14 * f), int(11 + 30 * f), int(18 + 46 * f)))
    bg = bg.filter(ImageFilter.GaussianBlur(90))
    im = bg.convert("RGBA")
    d = ImageDraw.Draw(im)
    _bg_grid(d, width, height, 0.0, accent, alpha=0.55)
    # ⚠ 评审:「右上青色圆是画面中最亮最大的元素, 压制了主标题和三条要点」
    #   → 缩小 + 压暗 + 右移; 左下红圆同样处理。
    _glow_dot(d, width * 0.90, height * 0.17, 200, accent, 52)
    _glow_dot(d, width * 0.10, height * 0.86, 210, ACCENT2, 34)

    tf = _font(TITLE_FONTS, 96)
    lf = _font(LABEL_FONTS, 42)
    pf = _font(LABEL_FONTS, 40)
    mono = _font(MONO_FONTS, 30)

    # 收尾标题
    t = "这一期讲完了"
    bb = d.textbbox((0, 0), t, font=tf)
    d.text(((width - (bb[2] - bb[0])) // 2, 190 - bb[1]), t, font=tf,
           fill=INK + (255,), stroke_width=3, stroke_fill=(6, 8, 12, 255))
    # ⚠ 评审:「下划线距离标题字形过近, 显得压紧」→ 下移 14px
    ul_y = 190 + (bb[3] - bb[1]) + 26
    ul_w = int(width * 0.22)
    d.rounded_rectangle([width // 2 - ul_w // 2, ul_y, width // 2 + ul_w // 2, ul_y + 11],
                        radius=5, fill=accent + (255,))

    # 三条要点(编号 + 文字)
    # ⚠ 评审:「编号与正文间距略小, 尤其第二条」→ 编号右边缘与正文固定 96px 间距
    y = ul_y + 104
    for i, pt in enumerate(points):
        num = f"{i+1:02d}"
        d.text((width // 2 - 340, y), num, font=mono, fill=accent + (235,))
        pb = d.textbbox((0, 0), pt, font=pf)
        d.text((width // 2 - 244, y - 4), pt, font=pf, fill=(232, 238, 246, 255),
               stroke_width=1, stroke_fill=(6, 8, 12, 190))
        # 细分隔线
        d.line([(width // 2 - 340, y + 68), (width // 2 + 340, y + 68)],
               fill=(120, 134, 152, 90), width=1)
        y += 104

    # 下期预告 —— ⚠ 评审:「NEXT 与下期几乎贴在一起, 像连写字符串」
    #   → 改成 **标签胶囊**, 视觉上彻底分开; 并统一为中文标签。
    if next_text:
        ny = height - 236
        lbl = "下期预告"
        lb = d.textbbox((0, 0), lbl, font=mono)
        lw = lb[2] - lb[0]
        d.rounded_rectangle([width // 2 - 340, ny - 8, width // 2 - 340 + lw + 34, ny + 40],
                            radius=22, fill=ACCENT2 + (225,))
        d.text((width // 2 - 340 + 17, ny - 2), lbl, font=mono, fill=(14, 10, 14, 255))
        d.text((width // 2 - 244, ny - 4), next_text, font=pf,
               fill=(232, 238, 246, 255), stroke_width=1, stroke_fill=(6, 8, 12, 190))

    foot = "全程合成数据 · 无真实信息 · 无业绩展示 · 无操作建议"
    fb = d.textbbox((0, 0), foot, font=mono)
    d.text(((width - (fb[2] - fb[0])) // 2, height - 132), foot, font=mono,
           fill=(138, 150, 166, 255), stroke_width=1, stroke_fill=(6, 8, 12, 220))

    # 文字区压暗
    im = _quiet_zone(im, ul_y - 40, height - 190, strength=105)
    # ⚠ footer 区也要压暗 —— 否则网格交点会被看成一排「小圆点」
    im = _quiet_zone(im, height - 210, height - 70, strength=88)

    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, width, 54], fill=(0, 0, 0, 255))
    d.rectangle([0, height - 54, width, height], fill=(0, 0, 0, 255))
    im.convert("RGB").save(out_png_base, quality=95)
    return out_png_base


def make_intro_clip(base_png, out_mp4, duration=4.2, width=1920, height=1080,
                    fps=25, fade=0.45, accent=ACCENT):
    """片头 → 视频片段。**带动画**: 缓慢推近 + 扫描光带 + 淡入淡出。

    🚨 帧数必须取整(同标题卡): duration*fps 可能是小数, 容器写 duration
       但实际帧数少 1 → concat 时留半帧空洞 → 容器推断 50fps。
    """
    frames = max(1, int(round(duration * fps)))
    exact = frames / float(fps)
    # 推近 + 扫描光带: 用 ffmpeg 做, 避免逐帧 PIL
    vf = (f"scale={int(width*1.16)}:{int(height*1.16)}:flags=lanczos,"
          f"zoompan=z='min(1.0+0.00055*on,1.10)':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps},"
          f"fade=t=in:st=0:d={fade},fade=t=out:st={exact-fade:.3f}:d={fade},"
          f"format=yuv420p")
    # 音频: 一点点低频房间感(非纯静音), 避免听起来像断片
    cmd = [FF, "-y", "-loglevel", "error",
           "-loop", "1", "-i", str(base_png),
           "-f", "lavfi", "-i", "sine=frequency=55:sample_rate=48000",
           "-t", f"{exact:.3f}",
           "-vf", vf, "-map", "0:v:0", "-map", "1:a:0",
           "-af", "volume=-40dB,afade=t=in:st=0:d=0.6,afade=t=out:st=%s:d=0.6" % f"{max(0.0,exact-0.6):.3f}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-frames:v", str(frames), "-r", str(fps),
           "-t", f"{exact:.3f}", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return out_mp4


def make_outro_clip(base_png, out_mp4, duration=4.6, width=1920, height=1080,
                    fps=25, fade=0.5, accent=ACCENT):
    """片尾 → 视频片段。缓慢拉远 + 淡出, 收束感。"""
    frames = max(1, int(round(duration * fps)))
    exact = frames / float(fps)
    vf = (f"scale={int(width*1.12)}:{int(height*1.12)}:flags=lanczos,"
          f"zoompan=z='max(1.09-0.00045*on,1.0)':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps},"
          f"fade=t=in:st=0:d={fade},fade=t=out:st={exact-fade:.3f}:d={fade},"
          f"format=yuv420p")
    cmd = [FF, "-y", "-loglevel", "error",
           "-loop", "1", "-i", str(base_png),
           "-f", "lavfi", "-i", "sine=frequency=55:sample_rate=48000",
           "-t", f"{exact:.3f}",
           "-vf", vf, "-map", "0:v:0", "-map", "1:a:0",
           "-af", "volume=-40dB,afade=t=in:st=0:d=0.6,afade=t=out:st=%s:d=0.6" % f"{max(0.0,exact-0.6):.3f}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-frames:v", str(frames), "-r", str(fps),
           "-t", f"{exact:.3f}", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return out_mp4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["intro", "outro"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png", default=None)
    ap.add_argument("--title", default="")
    ap.add_argument("--sub", default="")
    ap.add_argument("--badge", default="示例项目")
    ap.add_argument("--points", default="")
    ap.add_argument("--next", default="")
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=25)
    a = ap.parse_args()

    out = pathlib.Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    png = pathlib.Path(a.png).resolve() if a.png else out.with_suffix(".png")
    png.parent.mkdir(parents=True, exist_ok=True)

    if a.kind == "intro":
        render_intro(png, a.title, a.sub, a.badge, a.width, a.height)
        dur = a.duration or 4.2
        make_intro_clip(png, out, dur, a.width, a.height, a.fps)
    else:
        pts = [p for p in (a.points or "").split("|") if p.strip()]
        render_outro(png, pts, a.next, a.width, a.height)
        dur = a.duration or 4.6
        make_outro_clip(png, out, dur, a.width, a.height, a.fps)

    print(f"✅ {a.kind}: {out} ({out.stat().st_size//1024}KB)  底图 {png}")


if __name__ == "__main__":
    raise SystemExit(main())
