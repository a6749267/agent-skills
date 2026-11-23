#!/usr/bin/env python3
"""字体自查 —— 渲染前确认「字体能画出中文」+「字号够大」。

🚨 为什么需要这个工具(三个真实踩过的坑, 都不会报错):

坑1: **字体参数传错类型, 静默回落**
    写 `F(F, 25)` 而 `F` 其实是**函数**(不是字体路径) →
    `ImageFont.truetype(<function>, 25)` 抛 AttributeError →
    被 `except: return ImageFont.load_default()` 吞掉 →
    渲染出**满屏豆腐块(缺字方框)**, 没有任何报错。
    → 必须断言「传进去的是 str 路径」。

坑2: **Menlo 之类等宽英文体不含汉字**
    Menlo.ttc 只有拉丁字形 → 拿它画中文全是豆腐块。
    → 含中文的文本必须用中文字体。

坑3: **.ttc 字体集合的 index 陷阱**
    STHeiti Light.ttc: index 0 = Heiti **TC**(繁体), index 1 = Heiti **SC**(简体)。
    PIL 默认取 index 0 → 用繁体字形画简体字, 部分字形不对。
    Menlo.ttc: index 0 = Regular(0=Bold!) → 不能一律用 index=1。
    → .ttc 必须显式指定 index, 且不同字体不同。

用法:
    python check_font.py                    # 自查所有候选字体
    python check_font.py --text "未来函数检查" --size 26
"""
import argparse
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

# (路径, index, 说明)
CANDIDATES = [
    ("/System/Library/Fonts/STHeiti Light.ttc", 1, "黑体 Light 简体"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 1, "黑体 Medium 简体"),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0, "冬青黑体"),
    ("/System/Library/Fonts/PingFang.ttc", 0, "苹方"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0, "宋体 Black"),
    ("/System/Library/Fonts/Menlo.ttc", 0, "Menlo 英文等宽(无中文)"),
]

CJK_PROBE = "未来函数检查参数计算引擎"


def check(path, idx, note, probe=CJK_PROBE):
    p = pathlib.Path(path)
    if not p.exists():
        return None
    try:
        font = ImageFont.truetype(path, 28, index=idx)
    except Exception as e:
        print(f"  ✗ {path} idx={idx} 加载失败: {e}")
        return None

    # 用 cmap 判定是否含中文字形
    has_cjk = False
    try:
        from fontTools.ttLib import TTCollection, TTFont
        if path.endswith(".ttc"):
            f = TTCollection(path).fonts[idx]
        else:
            f = TTFont(path)
        cm = f.getBestCmap()
        has_cjk = any(ord(ch) in cm for ch in probe)
    except Exception:
        # fontTools 不可用 → 退回像素判定
        pass

    # 像素级判定: 渲染中文, 看有没有非背景像素
    im = Image.new("L", (400, 60), 0)
    d = ImageDraw.Draw(im)
    d.text((5, 10), probe, font=font, fill=255)
    ink = sum(1 for p in im.getdata() if p > 200)

    # 豆腐块判定: 豆腐块是空心方框, 笔画数远少于真汉字, 且整体宽度规整
    ok_pixels = ink > len(probe) * 8
    return dict(path=path, idx=idx, note=note, has_cjk=has_cjk,
                ink=ink, ok=has_cjk and ok_pixels)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--size", type=int, default=26)
    a = ap.parse_args()

    if a.text:
        # 渲染指定文本, 输出诊断
        print(f"检查: '{a.text}' @ {a.size}px")
        for path, idx, note in CANDIDATES:
            p = pathlib.Path(path)
            if not p.exists():
                continue
            try:
                f = ImageFont.truetype(path, a.size, index=idx)
            except Exception:
                continue
            im = Image.new("L", (1400, 90), 0)
            d = ImageDraw.Draw(im)
            d.text((10, 15), a.text, font=f, fill=255)
            ink = sum(1 for px in im.getdata() if px > 200)
            w = im.getbbox()
            width = (w[2] - w[0]) if w else 0
            per_char = width / max(1, len(a.text))
            flag = "✅" if ink > 0 and per_char < 60 else "⚠️"
            print(f"  {flag} {note:22s} ink={ink:6d} 宽/字≈{per_char:5.1f}px")
        return

    print("字体自查 —— 能否渲染中文")
    print("=" * 72)
    for path, idx, note in CANDIDATES:
        r = check(path, idx, note)
        if r is None:
            print(f"  – {note:22s} (不存在)")
            continue
        flag = "✅" if r["ok"] else "❌ 不可用于中文"
        print(f"  {flag} {note:22s} idx={r['idx']} ink={r['ink']:6d}  "
              f"含中文字形={r['has_cjk']}")
    print()
    print("🚨 铁律:")
    print("  1. truetype() 的第一个参数必须是 **str 路径**, 不能是函数/对象")
    print("     → 传错会静默回落 load_default() → 满屏豆腐块, 不报错")
    print("  2. 画中文必须用中文字体; Menlo 只有拉丁字形")
    print("  3. .ttc 必须显式 index: STHeiti→1(SC), Menlo→0(Regular)")
    print("  4. 1080p 横屏正文字号 ≥ 26px, 说明文字 ≥ 24px, 太小 B 站压缩后糊")


if __name__ == "__main__":
    main()
