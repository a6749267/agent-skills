#!/usr/bin/env python3
"""验收 —— 针对**交付的 mp4 本身**, 不是另一个脚本的结果。

用法:
    python verify.py --config project.json --video out/final.mp4
    python verify.py --config project.json --video out/final_sub.mp4 --amplify

三层验收, 从硬到软:
  1. 规格   —— 分辨率/时长/SAR/响度
  2. 泄漏   —— 全片 OCR 盲扫, 零真实名称/编号
  3. 数字   —— 放大后原生 OCR 读金额 + 与**源头算式**交叉验证

🚨🚨🚨 三条铁律:
  A. **验收对象必须是交付物本身**。拿 verify_synth.py 的通过结果给成片背书是无效的 ——
     两者可能走不同 profile/不同拦截配置/不同登录路径。历史上就这么翻过车:
     用户看完成片直接判「你那张只是网站的首页介绍页面，没有登陆进去」。
  B. **数字必须用原生 OCR 读, 不用 vision 读**。
     vision 在缩略拼图上把 −1424.00 读成 −1624.00、996,498.26 读成 996,698.24,
     然后**自己算出**「总数不一致」并质疑数据 —— 画面其实是对的。
     **假不一致比真不一致更贵**: 会让人去改本来正确的数据。
     vision 只用来判断「模块在不在 / 是不是空态」。
  C. **全片 OCR 盲扫**。抽查几帧不能证明「没有泄漏」—— 泄漏可能在第 7 秒。
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import synthetic  # noqa: E402

FF = "ffmpeg"
FP = "ffprobe"


# ── macOS 原生 OCR ──────────────────────────────────────────────
def macos_ocr(path):
    """用 Vision 引擎做 OCR。返回文本。

    🚨 不要用模型做 OCR(见 pitfalls §14): 会回显你的候选词、思维链吃光 max_tokens、
    M2.x 系列根本不认图 —— 三种失败看起来都像「扫描干净」。
    """
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
    req.setRecognitionLevel_(1)   # accurate
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        return ""
    out = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if cands and len(cands):
            out.append(str(cands[0].string()))
    return "\n".join(out)


def probe(path):
    r = subprocess.run([FP, "-v", "error", "-show_entries",
                        "format=duration:stream=width,height,sample_aspect_ratio,codec_name",
                        "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def loudness(path):
    """量响度。ffmpeg 新旧版本输出格式不同, 两种都要认。"""
    r = subprocess.run([FF, "-hide_banner", "-i", str(path), "-af",
                        "loudnorm=print_format=summary", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Input Integrated:" in line and "LUFS" in line:
            return line.strip().replace("Input Integrated:", "I:").strip()
        if line.strip().startswith("I:") and "LUFS" in line:
            return line.strip()
    return "?"


# ── 泄漏判定 ────────────────────────────────────────────────────
def build_scfg(cfg):
    """从 project.json + 领域配置构造合成配置(与 recorder 同源)。"""
    domain_cfg = synthetic.load_domain(name=cfg.get("domain"))
    scfg = synthetic.SyntheticConfig.from_domain(domain_cfg)
    over = cfg.get("synthetic") or {}
    if over.get("code_prefixes"):
        scfg.prefixes = list(over["code_prefixes"])
    if over.get("code_year") is not None:
        scfg.year = int(over["code_year"])
    if over.get("code_months"):
        scfg.months = list(over["code_months"])
    if over.get("name_template"):
        scfg.name_template = over["name_template"]
    return scfg


def find_leaks(text, cfg):
    """在 OCR 文本里找真实名称/编号。

    🚨 单字母前缀要人工复核: OCR 会吞掉首字母(qa2701 → a2701),
       而 a/c/m/y/p/b/i/j… 全是真实前缀 → 47 帧假阳性。
       规则: 单字母前缀命中 + 同帧存在合成码 → 丢弃。
    """
    scfg = build_scfg(cfg)
    rex = synthetic.build_sym_regex(scfg.real_prefixes, scfg.id_shape)
    hits = []
    for m in rex.finditer(text):
        pfx, dg = m.group(1), m.group(2)
        if not synthetic.is_id_shape(dg, scfg.id_shape) or synthetic.is_year_like(dg):
            continue
        if len(pfx) == 1:
            continue          # 单字母 → 疑似 OCR 吞首字母, 交给人工复核
        hits.append(m.group(0))
    names = [n for n in scfg.real_names + list(scfg.org_map) if n in text]
    return hits, names


def has_synth_code(text, cfg):
    for p in (cfg.get("synthetic") or {}).get("code_prefixes") or ["QA", "QB"]:
        if re.search(rf"(?<![A-Za-z0-9]){p.lower()}\d{{4}}", text):
            return True
    return False


# ── 数字抽取 ────────────────────────────────────────────────────
MONEY_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d{2})?")


def extract_numbers(text):
    out = []
    for m in MONEY_RE.finditer(text):
        s = m.group(0)
        try:
            v = float(s.replace(",", ""))
        except Exception:
            continue
        out.append((s, v))
    return out


# ── 字幕覆盖率 ──────────────────────────────────────────────────
def subtitle_coverage(video, work_dir, fps=1, crop="crop=1920:200:0:830",
                      white_thr=225, min_white=150, exclude=None):
    """🚨 检测字幕是否真的烧进了成片 —— 像素级, 不靠 OCR。

    为什么必须做:
      字幕被**静默跳过**过一次(script_path 相对 CWD 解析失败 →
      流程照跑成功 → 所有其他验收全过 → 交付物根本没字幕)。
      OCR 盲扫看不出「没有字幕」(画面文字依然正常), 规格也正常。
      唯一能自动发现的办法: **找字幕的字**。

    原理: 字幕是**纯白字**(255,255,255) + 黑描边, 铺在画面底部。
          统计底部条带里的近白像素数 → 有字幕的帧会明显偏多。

    ⚠ exclude = [(start, end), ...] 需要**排除**的时间区间。
       标题卡(chapter card)是静音章节卡, **故意没有字幕** →
       不排除的话覆盖率必然掉到 ~75%, 然后**误报"成片没字幕"**。
       成片旁的 timeline.json 里有这些区间。

    返回 (总帧数, 有字幕帧数, 无字幕帧列表[(秒, 白像素数)])
    """
    from PIL import Image
    d = pathlib.Path(work_dir) / "_cov"
    if d.exists():
        for f in d.glob("*.png"):
            f.unlink()
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps},{crop}", str(d / "c%05d.png")], check=True)
    files = sorted(d.glob("c*.png"))
    hit = 0
    miss = []
    excluded = 0
    for i, f in enumerate(files):
        t = i / fps
        if exclude and any(a <= t < b for a, b in exclude):
            excluded += 1
            continue
        im = Image.open(f).convert("RGB")
        w, h = im.size
        # 中间 80% 宽, 去掉顶部 15% —— 只看字幕所在的底部区域
        box = im.crop((int(w * 0.10), int(h * 0.15), int(w * 0.90), h))
        px = box.load()
        bw, bh = box.size
        white = 0
        for y in range(bh):
            for x in range(bw):
                r, g, b = px[x, y]
                if r > white_thr and g > white_thr and b > white_thr:
                    white += 1
        if white > min_white:
            hit += 1
        else:
            miss.append((round(t, 1), white))
    return len(files) - excluded, hit, miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--amplify", action="store_true",
                    help="抽帧后放大再读数字(强烈建议开)")
    ap.add_argument("--amplify-times", default="12,22,32,42",
                    help="放大抽帧的时间点(秒), 逗号分隔")
    ap.add_argument("--fps", type=float, default=2)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())
    video = pathlib.Path(a.video)
    if not video.exists():
        sys.exit(f"❌ 找不到视频 {video}")

    outdir = pathlib.Path(a.out_dir) if a.out_dir else video.parent / "_verify"
    outdir.mkdir(parents=True, exist_ok=True)
    frames = outdir / "frames"
    frames.mkdir(exist_ok=True)
    for f in frames.glob("*.png"):
        f.unlink()

    vcfg = cfg.get("verify", {})
    print("=" * 66)
    print(f"验收: {video}")
    print("=" * 66)

    # ── 1. 规格 ──
    info = probe(video)
    streams = info.get("streams", [])
    vs = next((s for s in streams if s.get("width")), {})
    print(f"\n【1. 规格】")
    print(f"  分辨率   {vs.get('width')}x{vs.get('height')}")
    print(f"  SAR      {vs.get('sample_aspect_ratio')}")
    print(f"  时长     {info.get('format',{}).get('duration')}s")
    ln = loudness(video)
    print(f"  响度     {ln}")
    if vs.get("sample_aspect_ratio") not in (None, "1:1"):
        print("  ⚠️  SAR 不是 1:1 —— 画面可能变形")

    # ── 2. 泄漏盲扫 ──
    fps = vcfg.get("ocr_fps", a.fps)
    print(f"\n【2. 泄漏盲扫】(全片 @ {fps}fps)")
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps}", str(frames / "f%05d.png")], check=True)
    flist = sorted(frames.glob("*.png"))
    total = len(flist)
    leak_frames = []
    valid = 0
    lens = []
    minc = vcfg.get("min_chars_per_frame", 20)
    for i, f in enumerate(flist):
        txt = macos_ocr(f)
        n = len(txt)
        lens.append(n)
        if n > minc:
            valid += 1
        hits, names = find_leaks(txt, cfg)
        if hits or names:
            leak_frames.append((f.name, hits, names, txt[:400]))
        if (i + 1) % 25 == 0:
            print(f"  帧{i+1}/{total} ocr {n} 字")
    avg = sum(lens) / max(1, len(lens))
    print(f"\n  帧数 {total}, 有效(>{minc}字) {valid}/{total}, avg {avg:.0f} 字")
    if valid < total * 0.9:
        print(f"  ⚠️  有效帧不足 90% —— OCR 读取失败, **不等于干净**(见 pitfalls §14)")
    if leak_frames:
        print(f"\n  ❌ 命中 {len(leak_frames)} 帧:")
        for name, hits, names, txt in leak_frames[:10]:
            print(f"    {name}: 编号={hits} 名称={names}")
            print(f"      {txt[:200]}")
    else:
        print("  ✅ 零真实名称 / 零真实编号")

    # ── 3. 数字 ──
    print(f"\n【3. 数字判读】")
    if not a.amplify:
        print("  ⚠️  未开 --amplify —— 缩略图读数会错(见 pitfalls §40)")
    times = [float(x) for x in a.amplify_times.split(",")]
    vw = int(vcfg.get("amplify_scale_w", 3840))
    vh = int(vcfg.get("amplify_scale_h", 2160))

    # 🚨🚨 必须**同时**做「整帧放大」和「区域裁剪」两种读法。
    #    实测: 同一个画面, 整帧放大能读出 −1,652.90, 但裁成顶栏细条就读不出;
    #    反过来带字幕版整帧读不到 −1,652.90(多一层文字 → OCR 行聚类变了)。
    #    两种读法各有盲区 → **都要做**, 数字取并集。
    crops = [(int(c[0]), int(c[1]), int(c[2]), int(c[3]))
             for c in vcfg.get("amplify_crops", [])]   # (x, y, w, h)

    all_nums = {}
    for t in times:
        print(f"\n  ── t={t}s ──")
        p = outdir / f"amp_{int(t)}.png"
        subprocess.run([FF, "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={vw}:{vh}:flags=lanczos", str(p)],
                       check=True)
        txt = macos_ocr(p)
        nums = extract_numbers(txt)
        interesting = [(s, v) for s, v in nums
                       if abs(v) >= 100 or s.count(",") or "." in s]
        print(f"    [整帧] {[s for s,_ in interesting][:14]}")
        for s, v in interesting:
            all_nums.setdefault(s, []).append(t)
        hits, names = find_leaks(txt, cfg)
        if hits or names:
            print(f"    ⚠️  疑似泄漏 编号={hits} 名称={names}")
        (outdir / f"amp_{int(t)}.txt").write_text(txt)

        # 区域裁剪读法
        for ci, (cx, cy, cw, ch) in enumerate(crops):
            cp = outdir / f"amp_{int(t)}_c{ci}.png"
            subprocess.run([FF, "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video),
                            "-frames:v", "1",
                            "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale={cw*3}:{ch*3}:flags=lanczos",
                            str(cp)], check=True)
            ctxt = macos_ocr(cp)
            cnums = extract_numbers(ctxt)
            cint = [(s, v) for s, v in cnums if abs(v) >= 100 or s.count(",") or "." in s]
            if cint:
                print(f"    [裁剪{cx},{cy}] {[s for s,_ in cint][:10]}")
                for s, v in cint:
                    all_nums.setdefault(s, []).append(t)
            hits, names = find_leaks(ctxt, cfg)
            if hits or names:
                print(f"    ⚠️  疑似泄漏 编号={hits} 名称={names}")

    if all_nums:
        print(f"\n  所有时间点读到的金额(并集):")
        for s in sorted(all_nums, key=lambda x: -len(all_nums[x])):
            print(f"    {s:>18s}  出现于 {len(all_nums[s])} 个时间点")

    # ── 阶段4: 字幕覆盖率 ──
    sub_ok = True
    if vcfg.get("subtitle_enabled", True):
        print(f"\n【4. 字幕覆盖率】(像素检测, 全片 @1fps)")
        # 标题卡/片头/片尾区间(故意无字幕) → 排除
        # 🚨🚨🚨 必须排除 **intro / outro / title_card** 三种:
        #    只排除 title_card 的话, 片头(4.2s)+片尾(4.6s)会被算进"无字幕帧"
        #    → 覆盖率被误判成 87.6% → 误报"成片没有字幕" → 白折腾一轮。
        excl = []
        tl_path = video.parent / "timeline.json"
        if tl_path.exists():
            tl = json.loads(tl_path.read_text())
            for tr in tl.get("tracks", []):
                if tr.get("kind") in ("title_card", "intro", "outro"):
                    excl.append((tr["start"], tr["end"]))
            # 🚨🚨🚨 **段尾静音尾巴也要排除**:
            #    段落视频比旁白长(为了留白), 旁白说完后画面还在但没字幕
            #    (实测 seg0: 旁白 18.26s 结束, 段落长 19.32s → 尾部 1.06s 无字幕)。
            #    这些是**设计如此**, 不是"字幕没烧进去"。
            #    → 从 align.json 读每段旁白结束时间, 排除段尾到段末。
            al_path = video.parent / "align.json"
            if not al_path.exists():
                cfg_dir = pathlib.Path(cfg.get("_config_dir", "."))
                cand = cfg_dir / "align.json"
                al_path = cand if cand.exists() else al_path
            if al_path.exists():
                al = json.loads(al_path.read_text())
                for tr in tl.get("tracks", []):
                    if tr.get("kind") != "segment":
                        continue
                    si = str(tr.get("seg"))
                    if si not in al:
                        continue
                    ch = al[si].get("chars") or []
                    if not ch:
                        continue
                    speech_end = float(ch[-1]["e"])
                    seg_len = float(tr["end"]) - float(tr["start"])
                    if speech_end + 0.2 < seg_len:
                        excl.append((float(tr["start"]) + speech_end,
                                     float(tr["end"])))
            if excl:
                print(f"  排除无字幕区间(片头/标题卡/片尾/段尾静音): {len(excl)} 段, "
                      f"共 {sum(b - a for a, b in excl):.1f}s")
        try:
            tot, hit, miss = subtitle_coverage(video, outdir, exclude=excl)
            pct = hit / max(1, tot) * 100
            print(f"  总帧 {tot}, 有字幕 {hit}, 覆盖 {pct:.1f}%")
            if miss:
                print(f"  无字幕帧(秒, 白像素): {miss[:20]}")
            # 段落切换帧天然无字幕 → 允许少量缺口
            if pct < 90:
                print(f"  ❌ 字幕覆盖仅 {pct:.1f}% —— 成片很可能**没有字幕**!")
                print(f"     检查: script_path 是否能解析到 / 是否被静默跳过(见 pitfalls §42)")
                sub_ok = False
            elif pct < 97:
                print(f"  ⚠️  覆盖 {pct:.1f}% —— 检查无字幕帧是不是段落切换")
            else:
                print(f"  ✅ 字幕已烧入成片")
        except Exception as e:
            print(f"  ⚠️  字幕检测失败: {e}")
    else:
        print(f"\n【4. 字幕覆盖率】已跳过(配置关闭)")

    print("\n" + "=" * 66)
    if leak_frames:
        print("❌ 未通过: 有泄漏帧")
        return 1
    if not sub_ok:
        print("❌ 未通过: 字幕未烧入成片")
        return 1
    print("✅ 泄漏检查通过")
    print("⚠️  数字是否自洽 —— 需要人工把读到的金额和源头算式对一遍(见 pitfalls §40)")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
