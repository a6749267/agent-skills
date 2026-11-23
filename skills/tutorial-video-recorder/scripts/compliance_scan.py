#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""领域合规词扫描 —— 视频/文案被平台判「专业领域内容」后的自检器。

用途：内容被平台判为某类专业领域（金融/医疗/法律…）后，把标题/简介/画面文案/代码里的
领域语义词找出来并清零。

为什么不能只 grep 中文：
  代码块的**英文标识符与注释**同样带领域语义
  （contract / turnover / open_interest / slippage / fee / mult），
  中文词表清零了它们照样在画面上，机器审核照样判该领域。

分级：
  STRONG  —— 必清。带这些词基本等同于自报「这是该领域专业内容」。
  WEAK    —— 行业通用工程术语。去掉反而不专业，
             可保留，但要在画面上用代码/架构图压过语义。

词表在 `assets/compliance/<domain>.json`，按领域切换：
  python compliance_scan.py <文件或目录> --domain finance
  python compliance_scan.py <文件或目录> --domain default

退出码：0 = 无 STRONG 命中；1 = 有 STRONG 命中（不许发）
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
COMPLIANCE_DIR = HERE.parent / "assets" / "compliance"

# ── 内置默认词表（只做兜底，正式词表在 assets/compliance/） ────
DEFAULT_TABLES = {
    "strong_cn": [],
    "strong_en": [],
    "weak_cn": [],
    "weak_en": [],
}

TEXT_SUFFIX = {".md", ".txt", ".json", ".py", ".js", ".ts", ".html", ".yaml", ".yml"}
SKIP_DIR = {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"}


def load_tables(domain="default"):
    """加载词表。找不到就用内置空表(不误报)。"""
    p = COMPLIANCE_DIR / f"{domain}.json"
    if not p.exists():
        import warnings
        warnings.warn(f"合规词表 {domain} 不存在 → 用空表(不会误报, 但也查不出)")
        return dict(DEFAULT_TABLES)
    d = json.loads(p.read_text(encoding="utf-8"))
    return {
        "strong_cn": d.get("strong_cn", []),
        "strong_en": d.get("strong_en", []),
        "weak_cn": d.get("weak_cn", []),
        "weak_en": d.get("weak_en", []),
    }


def _compile(lst):
    if not lst:
        return None
    return re.compile("|".join(map(re.escape, lst)))


def scan_text(text, show_weak, only_code, cn_re=None, en_re=None,
              w_cn_re=None, w_en_re=None):
    strong, weak = [], []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        is_code = bool(re.match(r"[#/*]|\w+\s*=", line)) or "=" in line
        if only_code and not is_code:
            continue
        if cn_re:
            for m in cn_re.finditer(line):
                strong.append((m.group(0), i, stripped))
        if en_re:
            for m in en_re.finditer(line):
                strong.append((m.group(0), i, stripped))
        if show_weak:
            if w_cn_re:
                for m in w_cn_re.finditer(line):
                    weak.append((m.group(0), i, stripped))
            if w_en_re:
                for m in w_en_re.finditer(line):
                    weak.append((m.group(0), i, stripped))
    return strong, weak


def iter_files(paths):
    for p in paths:
        path = pathlib.Path(p)
        if path.is_file():
            yield path
        elif path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file() and f.suffix.lower() in TEXT_SUFFIX:
                    if any(s in f.parts for s in SKIP_DIR):
                        continue
                    yield f


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    weak = "--weak" in argv
    only_code = "--only-code" in argv
    domain = "default"
    if "--domain" in argv:
        i = argv.index("--domain")
        if i + 1 < len(argv):
            domain = argv[i + 1]

    if not args:
        print(__doc__)
        return 2

    tables = load_tables(domain)
    cn_re = _compile(tables["strong_cn"])
    en_re = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, tables["strong_en"]))
                       + r")(?![A-Za-z0-9_])", re.I) if tables["strong_en"] else None
    w_cn_re = _compile(tables["weak_cn"])
    w_en_re = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, tables["weak_en"]))
                         + r")(?![A-Za-z0-9_])", re.I) if tables["weak_en"] else None

    if not (cn_re or en_re):
        print(f"⚠️  领域 '{domain}' 的词表是空的 → 什么都没查。")
        if domain == "default":
            print("    提示: 用 --domain <你的领域> 指定词表, 或在 assets/compliance/ 加一份。")
        return 0

    total = 0
    for f in iter_files(args):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        strong, weak_hits = scan_text(txt, weak, only_code, cn_re, en_re, w_cn_re, w_en_re)
        if strong:
            total += len(strong)
            print(f"\n🔴 {f}")
            for w, i, s in strong[:20]:
                print(f"   {i}: {w!r}  |  {s[:110]}")
            if len(strong) > 20:
                print(f"   … 还有 {len(strong)-20} 处")
        if weak_hits:
            print(f"\n🟡 {f}  (WEAK {len(weak_hits)} 处, 可保留)")
            for w, i, s in weak_hits[:8]:
                print(f"   {i}: {w!r}  |  {s[:110]}")

    print()
    if total:
        print(f"❌ STRONG 命中 {total} 处 —— 清零后再发。")
        return 1
    print("✅ 无 STRONG 命中。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
