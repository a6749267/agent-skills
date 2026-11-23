#!/usr/bin/env python3
"""交付物脱敏自检 —— 发出去之前扫一遍。

🚨 为什么必须自带而不是靠外面的检查器:
   外面那个检查器扫 **mp4 二进制**时会报几百处 BLOCK(把压缩码流里的随机字节
   当成普通词) → **全是误报**, 已验收的成片也一样会报几百处。  # leak-ok
   → 正确的做法:
     · 文本类物料(文案/简介/标题/标签) → 全量扫
     · 视频/音频 → **不能扫二进制**, 要抽帧 OCR + 转写
   本脚本默认只扫文本类, 视频类用 --ocr/--transcribe 显式开启。

🚨 文档里写绝对路径 → 检查器会判 BLOCK(退出码 1), 但不在正文里。
   → 本脚本扫 skill 自己的文档时会命中 → 文档里一律用**占位命令**。

🚨🚨 敏感词表**不写死在代码里** —— 写死等于把你的私密身份公开。
   本脚本自带的是**通用规则**(本机路径/真密钥/凭据赋值/股票代码形状…);
   你自己的私有词(项目名/内部代号/域名)放在 `assets/leak_patterns.json`:
     {
       "block": [["你的私有项目名", "私有项目"], ["你的内部代号", "内部代号"]],
       "warn":  [["某个易混淆的词", "自定义"]]
     }
   用法:
     python scan_leak.py publish_copy.md
     python scan_leak.py --patterns my_patterns.json publish_copy.md
     python scan_leak.py --ocr video.mp4 --frames 8
     python scan_leak.py --transcribe video.mp4
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PATTERNS = HERE.parent / "assets" / "leak_patterns.json"

# ── BLOCK 级(出现即失败) ──
# ⚠ 这些规则行本身含敏感词 → 扫描自己会命中 → 每行加 `leak-ok` 豁免。
#    通用规则: 与使用者身份无关的泄漏形态。
BLOCK_PATTERNS = [
    (r"/Users/[a-zA-Z0-9._-]+|/Volumes/[A-Za-z0-9._-]+|/workspace/|/\.hermes|~/\.hermes", "本机路径"),  # leak-ok
    (r"sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY", "真密钥"),  # leak-ok
    (r"(?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']", "疑似凭据赋值"),  # leak-ok
    (r"年化\s*[-+]?\d|CAGR\s*[-+]?\d|胜率\s*\d+(\.\d+)?\s*%|最大回撤\s*\d+(\.\d+)?\s*%|夏普\s*\d", "业绩数字"),  # leak-ok
]

# ── WARN 级(需人工判断) ──
WARN_PATTERNS = [
    (r"Hermes|hermes-agent|OpenClaw|openclaw", "工具栈"),  # leak-ok
    (r"\b\d{6}\.(?:SZ|SH|sz|sh)\b", "股票代码"),  # leak-ok
    (r"(?<![A-Za-z0-9])(?-i:[A-Z]{1,3}|[a-z]{1,2})(?:[0-9](?:0[1-9]|1[0-2])|[0-9]{2}(?:0[1-9]|1[0-2]))(?![0-9])", "编号代码"),  # leak-ok
    (r"(?:收益?|盈亏|利润|盈利|浮盈|浮亏|总收益)\s*[:：]?\s*[+－-]?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?", "业绩金额"),  # leak-ok
    (r"止损\s*[-−]?\d+(?:\.\d+)?\s*%|止盈\s*[-−]?\d+(?:\.\d+)?\s*%", "阈值参数"),  # leak-ok
]

TEXT_EXT = {".md", ".txt", ".json", ".py", ".sh", ".yaml", ".yml", ".cfg", ".ini", ".toml"}


def load_custom_patterns(path=None):
    """加载外部私有词表 → (block_patterns, warn_patterns)。

    格式: {"block": [[正则, 类别], ...], "warn": [[正则, 类别], ...]}
    加载失败返回空列表(不影响内置通用规则)。
    """
    p = pathlib.Path(path) if path else DEFAULT_PATTERNS
    if not p.exists():
        return [], []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️  私有词表加载失败({e}) → 只用通用规则")
        return [], []
    blk = [(r[0], r[1]) for r in d.get("block", []) if len(r) >= 2]
    wrn = [(r[0], r[1]) for r in d.get("warn", []) if len(r) >= 2]
    return blk, wrn


def load_allowlist(paths=None):
    """加载白名单(命中即放行)。

    白名单是拿「规则匹配到的那个片段」去搜的, 不是拿整行。
    所以正则不要带尾斜杠 —— 匹配到的片段是 `~/.hermes` 而不是 `~/.hermes/`。

    来源:
      · --allow <file> 指定的文件
      · 目标目录下名为 leak_allow.txt 的文件(向上找 3 层)
    """
    allow = []
    for p in (paths or []):
        try:
            lines = pathlib.Path(p).read_text(encoding="utf-8").split("\n")
        except Exception:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                allow.append(re.compile(line))
            except re.error:
                pass
    return allow


def scan_text(path, block_pats=None, warn_pats=None, allow=None):
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    hits = []
    bp = list(BLOCK_PATTERNS) + list(block_pats or [])
    wp = list(WARN_PATTERNS) + list(warn_pats or [])
    allow = allow or []

    def _allowed(fragment):
        return any(rx.search(fragment) for rx in allow)

    for i, line in enumerate(txt.split("\n"), 1):
        if "leak-ok" in line:
            continue
        for pat, cat in bp:
            for m in re.finditer(pat, line):
                if _allowed(m.group(0)):
                    continue
                hits.append(("BLOCK", cat, i, line.strip()[:120]))
        for pat, cat in wp:
            for m in re.finditer(pat, line):
                if _allowed(m.group(0)):
                    continue
                hits.append(("WARN", cat, i, line.strip()[:120]))
    return hits


def scan_mp4_binary(path):
    """扫 mp4 二进制 —— 会大量误报, 仅作提示, 不作为判据。"""
    try:
        data = path.read_bytes()
    except Exception:
        return []
    hits = []
    txt = data.decode("latin-1", "ignore")
    for pat, cat in BLOCK_PATTERNS:
        n = len(re.findall(pat, txt))
        if n:
            hits.append(("INFO", f"{cat}(二进制噪声, 多为误报)", 0, f"{n} 处"))
    return hits


def ocr_frames(video, count=8):
    """抽帧 → OCR(需 macOS Vision; 没装就跳过)。"""
    import tempfile
    try:
        from Foundation import NSURL  # noqa
        from Vision import VNRecognizeTextRequest, VNImageRequestHandler
        from Quartz import CIImage
    except ImportError:
        print("  ⚠️ 没装 PyObjC, 跳过 OCR(用视觉工具人工核对)")
        return []
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    try:
        total = float(dur)
    except ValueError:
        return []
    hits = []
    code = '''
import sys
from Foundation import NSURL
from Vision import VNRecognizeTextRequest, VNImageRequestHandler
from Quartz import CIImage
url = NSURL.fileURLWithPath_(sys.argv[1])
ci = CIImage.imageWithContentsOfURL_(url)
h = VNImageRequestHandler.alloc().initWithCIImage_options_(ci, None)
r = VNRecognizeTextRequest.alloc().init()
r.setRecognitionLanguages_(["zh-Hans","en-US"])
r.setRecognitionLevel_(1)
r.setUsesLanguageCorrection_(False)
h.performRequests_error_([r], None)
for o in r.results():
    print(o.topCandidates_(1)[0].stringValue())
'''
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        script = f.name
    for i in range(count):
        ts = total * (i + 0.5) / count
        png = f"/tmp/_sl_{i}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}",
                        "-i", str(video), "-frames:v", "1", png], check=False)
        out = subprocess.run([sys.executable, script, png], capture_output=True, text=True).stdout
        for line in out.split("\n"):
            line = line.strip()
            if not line:
                continue
            for pat, cat in BLOCK_PATTERNS:
                if re.search(pat, line):
                    hits.append(("BLOCK", f"画面OCR:{cat}", i, line[:120]))
    return hits


def transcribe_audio(video):
    """转写音轨 → 查敏感词。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  ⚠️ 没装 faster_whisper, 跳过转写")
        return []
    import warnings
    warnings.filterwarnings("ignore")
    wav = "/tmp/_sl_audio.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", wav], check=False)
    wm = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = wm.transcribe(wav, language="zh", beam_size=5)
    txt = "".join(s.text for s in segs)
    hits = []
    for pat, cat in BLOCK_PATTERNS + WARN_PATTERNS:
        for m in re.finditer(pat, txt):
            hits.append(("WARN" if cat in ("工具栈",) else "BLOCK", cat, 0, txt[max(0, m.start()-20):m.end()+20]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--ocr", action="store_true", help="视频: 抽帧 OCR")
    ap.add_argument("--transcribe", action="store_true", help="视频: 转写音轨")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--json", default="")
    ap.add_argument("--patterns", default="",
                    help="私有词表 json(默认 assets/leak_patterns.json, 不存在则只用通用规则)")
    ap.add_argument("--allow", action="append", default=[],
                    help="白名单文件(每行一个正则, 命中即放行)。默认自动找目标目录下的 leak_allow.txt")
    args = ap.parse_args()

    all_hits = []
    files = []
    blk_pats, wrn_pats = load_custom_patterns(args.patterns or None)
    if blk_pats or wrn_pats:
        print(f"  私有词表: BLOCK {len(blk_pats)} 条 / WARN {len(wrn_pats)} 条")

    # 白名单: 显式指定 + 自动找目标树里所有 leak_allow.txt
    allow_files = list(args.allow)
    seen = set()
    for p in args.paths:
        pp = pathlib.Path(p).resolve()
        if pp.is_dir():
            for f in pp.rglob("leak_allow.txt"):
                if f.is_file() and str(f) not in seen:
                    seen.add(str(f)); allow_files.append(str(f))
        else:
            base = pp.parent
            for up in [base, base.parent, base.parent.parent]:
                f = up / "leak_allow.txt"
                if f.exists() and str(f) not in seen:
                    seen.add(str(f)); allow_files.append(str(f))
    allow = load_allowlist(allow_files)
    if allow:
        print(f"  白名单: {len(allow)} 条规则 ← {', '.join(pathlib.Path(x).name for x in allow_files)}")
    for p in args.paths:
        pp = pathlib.Path(p)
        if pp.is_dir():
            for f in pp.rglob("*"):
                if f.is_file() and f.suffix.lower() in TEXT_EXT:
                    files.append(f)
        elif pp.is_file():
            files.append(pp)

    for f in files:
        if f.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
            hits = scan_mp4_binary(f)
        else:
            hits = scan_text(f, blk_pats, wrn_pats, allow)
        for h in hits:
            all_hits.append((str(f),) + h)

    if args.ocr or args.transcribe:
        for p in args.paths:
            pp = pathlib.Path(p)
            if pp.is_file() and pp.suffix.lower() in (".mp4", ".mov", ".mkv"):
                if args.ocr:
                    for h in ocr_frames(pp, args.frames):
                        all_hits.append((str(pp),) + h)
                if args.transcribe:
                    for h in transcribe_audio(pp):
                        all_hits.append((str(pp),) + h)

    blocks = [h for h in all_hits if h[1] == "BLOCK"]
    warns = [h for h in all_hits if h[1] == "WARN"]
    infos = [h for h in all_hits if h[1] == "INFO"]

    for f, lvl, cat, ln, txt in all_hits:
        if lvl == "INFO":
            continue
        print(f"{'❌' if lvl == 'BLOCK' else '⚠️ '} [{lvl}] {cat}  {f}:{ln}")
        print(f"    {txt}")

    print(f"\n=== BLOCK {len(blocks)} / WARN {len(warns)} / INFO {len(infos)} ===")
    if infos:
        print("(INFO 是二进制噪声提示, 不作为判据)")
    if blocks:
        print("❌ 有 BLOCK —— 不许发出去")
        if args.json:
            pathlib.Path(args.json).write_text(
                json.dumps([list(h) for h in all_hits], ensure_ascii=False, indent=2),
                encoding="utf-8")
        return 1
    print("✅ 干净")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
