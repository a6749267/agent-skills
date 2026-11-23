#!/usr/bin/env python3
"""豆腐块扫描：用 **AST 解析**源码，逐个 d.text/d.multiline_text 调用检查字体。

为什么必须用 AST
----------------
正则匹配字面量有三个致命盲区(实测都踩了):
  1. 字符串**跨行**时正则的 [^)]*? 会提前截断 → 漏报
  2. 字面量里**含拼接/变量**时正则完全匹配不到 → 漏报
  3. 扫"源码里所有中文字符串"会把注释/docstring/FM() 的正常中文
     全算成误报(实测 2206 处)

AST 拿到的是**真实的调用结构**: 每个 Call 节点的 func 名、args、keywords。
再从 keywords 里取 font= 的值, 解析出字体常量 → 查 cmap。

用法:
    python check_tofu.py <源码文件> [<源码文件> ...]
"""
import ast
import sys
import pathlib

FONT_FILES = {
    "MONO": ("/System/Library/Fonts/Menlo.ttc", 0),
    "REG": ("/System/Library/Fonts/STHeiti Light.ttc", 1),
    "BOLD": ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
}
EXTRA_FONTS = {
    "F9.F_MONO": ("/System/Library/Fonts/Menlo.ttc", 0),
    "F9.F_REG": ("/System/Library/Fonts/STHeiti Light.ttc", 1),
    "F9.F_BOLD": ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
}
# Menlo 有这些符号字形; 标点/箭头/数学符号允许缺失
PUNCT = set("：，、。；！？（）「」【】《》·×÷≈≥≤—…“”‘’→←↑↓√±°−｜+=%*/#()[]<>.,:;!?\"'`|~@^&_ ")
CJK = lambda s: any("一" <= c <= "鿿" or "　" <= c <= "〿"
                    or "＀" <= c <= "￯" for c in s)


def load_cmap(path, index):
    from fontTools.ttLib import TTCollection, TTFont
    if path.endswith(".ttc"):
        f = TTCollection(path, lazy=True).fonts[index]
    else:
        f = TTFont(path, lazy=True)
    cm = set()
    for table in f["cmap"].tables:
        cm |= set(table.cmap.keys())
    return cm


def font_key(node):
    """从 font= 的 AST 节点解析出 (路径, index)。

    支持:
      F(MONO, 30) / F(BOLD, 30) / F(REG, 30)
      FM("...", 30)          → None（自动选字体，含中文会走 REG，安全）
      F9.F_MONO / BOLD / REG → 模块级常量
    """
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "F":
            if node.args and isinstance(node.args[0], ast.Name):
                return FONT_FILES.get(node.args[0].id)
        if isinstance(fn, ast.Attribute):
            return EXTRA_FONTS.get(ast.unparse(fn))
        return None
    if isinstance(node, ast.Name):
        return FONT_FILES.get(node.id) or EXTRA_FONTS.get(node.id)
    if isinstance(node, ast.Attribute):
        return EXTRA_FONTS.get(ast.unparse(node))
    return None


def literal_strings(node):
    """取出节点里的所有字符串常量（拼接也能拿到）。"""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def main():
    files = sys.argv[1:]
    if not files:
        print("用法: python check_tofu.py <源码文件> [...]")
        return 2

    cmaps = {}
    bad = []
    checked = 0
    for fp in files:
        p = pathlib.Path(fp)
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if fname not in ("text", "multiline_text"):
                continue
            # 找 font= 关键字
            fk = None
            for kw in node.keywords:
                if kw.arg == "font":
                    fk = kw.value
            if fk is None:
                continue
            key = font_key(fk)
            if key is None:
                continue   # FM() 自动选字体 → 安全
            # 取文本参数: 位置参数[1]（坐标是 [0]）或 font= 前面的那个
            txt_node = None
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    txt_node = a
                    break
            if txt_node is None:
                continue
            s = txt_node.value
            if not CJK(s):
                continue
            checked += 1
            fpath, fidx = key
            ck = (fpath, fidx)
            if ck not in cmaps:
                try:
                    cmaps[ck] = load_cmap(fpath, fidx)
                except Exception as e:
                    print(f"⚠️  读字体失败 {fpath}: {e}")
                    cmaps[ck] = set()
            cm = cmaps[ck]
            for ch in s:
                if ch in PUNCT or ch.isspace():
                    continue
                if ord(ch) not in cm:
                    bad.append((p.name, node.lineno, ch, hex(ord(ch)),
                                ast.unparse(fk), s[:50]))

    print(f"检查了 {checked} 处『含中文 + 固定字体』的绘制调用")
    if bad:
        print(f"❌ 发现 {len(bad)} 处豆腐块风险（该字体无此字形）:")
        for b in bad:
            print(f"   {b[0]}:{b[1]}  '{b[2]}' {b[3]}  字体={b[4]}  in: {b[5]}")
        return 1
    print("✅ 未发现豆腐块风险")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
