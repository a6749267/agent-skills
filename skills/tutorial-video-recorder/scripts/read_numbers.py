#!/usr/bin/env python3
r"""成片数字判读 —— 两种读法取并集, 并与源头算式交叉验证。

用法:
    python read_numbers.py --video out/final_sub.mp4 --times 12,22,32 \
        --crop 250,0,800,60 --crop 250,120,900,400 \
        --expect -1400.00 --expect 1020000.00

为什么要**两种**读法:
    Vision OCR 的**行聚类**对输入布局敏感 —— 同一个画面:
      · 整帧放大(3840x2160) 能读出「−1,652.90」
      · 把顶栏裁成细条单独读, 读不出
    反过来, 带字幕的版本整帧读不到(多一层文字 → 行聚类变了)。
    **两种读法各有盲区 → 都要做, 数字取并集。**

为什么要**交叉验证**:
    只有「一个工具的读数」= 不可信。OCR 读出的每个金额,
    都要能用**源头算式**独立复算对上(DB 明细 × 单价 × 数量)。
    算式对得上 + OCR 读数对得上 = 真一致。

⚠ 别用模型读金额 —— 缩略拼图上的 vision 会把 −1200.00 读成 −1400.00,
   然后自己算出「总数不一致」去质疑**本来正确**的数据。
   vision 只用来判断「模块在不在 / 是不是空态」。

依赖: macOS 原生 Vision(pyobjc-framework-Vision + Quartz) + ffmpeg
"""
import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FF = "ffmpeg"
MONEY_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d{2})?")


def macos_ocr(path):
    """macOS 原生 Vision OCR。返回文本。"""
    import objc
    from Vision import VNRecognizeTextRequest, VNImageRequestHandler
    from Quartz import CIImage
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(str(pathlib.Path(path).resolve()))
    ci = CIImage.imageWithContentsOfURL_(url)
    if ci is None:
        return ""
    handler = VNImageRequestHandler.alloc().initWithCIImage_options_(ci, None)
    req = VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(1)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return ""
    out = []
    for obs in (req.results() or []):
        c = obs.topCandidates_(1)
        if c and len(c):
            out.append(str(c[0].string()))
    return "\n".join(out)


def extract(text):
    """抽出金额。返回 [(原样字符串, 数值), ...]"""
    res = []
    for m in MONEY_RE.finditer(text or ""):
        s = m.group(0)
        try:
            res.append((s, float(s.replace(",", ""))))
        except Exception:
            pass
    return res


def interesting(pairs):
    """只留像金额的: 带小数点 / 带千分位 / 绝对值 >= 100"""
    return [(s, v) for s, v in pairs if abs(v) >= 100 or "," in s or "." in s]


def grab(video, t, out_png, crop=None, scale=(3840, 2160)):
    """抽一帧(可选裁剪区域)并放大。"""
    vf = []
    if crop:
        x, y, w, h = crop
        vf.append(f"crop={w}:{h}:{x}:{y}")
    vf.append(f"scale={scale[0]}:{scale[1]}:flags=lanczos")
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video),
                    "-frames:v", "1", "-vf", ",".join(vf), str(out_png)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--times", default="12,22,32")
    ap.add_argument("--crop", action="append", default=[],
                    help="x,y,w,h —— 可重复, 用于区域裁剪读法")
    ap.add_argument("--scale", default="3840,2160")
    ap.add_argument("--expect", action="append", default=[], type=float,
                    help="期望读到的金额(可重复) —— 用于交叉验证")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    video = pathlib.Path(a.video)
    if not video.exists():
        sys.exit(f"找不到视频 {video}")
    outdir = pathlib.Path(a.out_dir) if a.out_dir else video.parent / "_numbers"
    outdir.mkdir(parents=True, exist_ok=True)
    scale = tuple(int(x) for x in a.scale.split(","))

    crops = []
    for c in a.crop:
        parts = [int(x) for x in c.split(",")]
        if len(parts) == 4:
            crops.append(tuple(parts))

    times = [float(x) for x in a.times.split(",")]
    seen = {}          # 数值 -> 出现次数

    for t in times:
        print(f"\n-- t={t}s --")
        # 读法1: 整帧放大
        p = outdir / f"f_{t}.png"
        grab(video, t, p, None, scale)
        pairs = interesting(extract(macos_ocr(p)))
        print(f"  [整帧] {[s for s, _ in pairs][:16]}")
        for _, v in pairs:
            seen[v] = seen.get(v, 0) + 1
        # 读法2: 区域裁剪
        for i, c in enumerate(crops):
            cp = outdir / f"f_{t}_c{i}.png"
            grab(video, t, cp, c, (c[2] * 3, c[3] * 3))
            cpairs = interesting(extract(macos_ocr(cp)))
            if cpairs:
                print(f"  [裁剪{c}] {[s for s, _ in cpairs][:10]}")
                for _, v in cpairs:
                    seen[v] = seen.get(v, 0) + 1

    print("\n=== 金额并集(出现次数降序) ===")
    for v, n in sorted(seen.items(), key=lambda kv: -kv[1])[:30]:
        print(f"  {v:>18,.2f}   x{n}")

    if a.expect:
        print("\n=== 交叉验证(期望值是否被读到) ===")
        ok = True
        for e in a.expect:
            hit = any(abs(v - e) < 0.011 for v in seen)
            print(f"  {'OK ' if hit else 'MISS'} {e:,.2f}")
            ok = ok and hit
        print("\n" + ("全部期望值都读到了" if ok
                      else "有期望值没读到 -- 可能是画面变了, 或该读法有盲区"))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
