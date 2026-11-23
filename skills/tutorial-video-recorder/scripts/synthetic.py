#!/usr/bin/env python3
"""合成引擎 —— 把真实 API 响应替换成合成 payload。

这是管线里**唯一**决定「画面上长什么样」的地方, 也是最容易出岔子的地方。
分成三层, 从外到内:

  L1  bundle  →  改 JS 源码里的**硬编码字面量**(前端自己带的真实名称/编号)
  L2  数据类  →  整个响应替换成合成 payload(列表/报价/明细)
  L3  业务类  →  放行真实后端(数据库里的合成记录), 但字段级兜底清洗 + 汇总重算

为什么分三层:
  - 只做 L2 → 前端 bundle 里写死的编号照样出现在画面上(DOM 洗不掉)
  - 只做 L3 → 数据接口还是真实数据 → 泄漏面是整个数据面
  - 只做 L1 → 业务接口返回真实库里的东西 → 泄漏

🚨 三条铁律:
  1. **一份实现, 录制和验收共用** —— 见 references/pitfalls.md §34。
     历史上 verify 脚本 import 了清洗模块却没在路由里用, 报「零泄漏」是侥幸。
  2. **合成前缀不能撞真实前缀** —— 见 §5。
  3. **注入点要能断言真的生效** —— 单元测试通过 ≠ 路由生效, 见 §33。

领域差异全部在 `assets/domains/<name>.json`:
  - 真实编号前缀 / 真实名称 / 机构名映射 → 词表
  - 标识符形状(几位数字、几位前缀) → 守卫参数
换 app = 换一份配置, 本文件不动。
"""
import json
import re
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
DOMAINS_DIR = HERE.parent / "assets" / "domains"


# ─────────────────────────────────────────────────────────────
# 领域配置加载
# ─────────────────────────────────────────────────────────────
def load_domain(name=None, path=None):
    """加载领域配置。

    name: assets/domains/ 下的文件名(不含 .json)
    path: 直接给 json 路径(优先)
    找不到 → 返回中性基线(空词表), 并在 stderr 提示。
    """
    p = None
    if path:
        p = pathlib.Path(path)
    elif name:
        p = DOMAINS_DIR / f"{name}.json"
    if p is None or not p.exists():
        import sys
        print(f"⚠️  领域配置 {name or path} 不存在 → 用中性基线(无词表, 不会误清洗)",
              file=sys.stderr)
        return {
            "real_code_prefixes": [], "real_display_names": [], "org_map": {},
            "synthetic": {"code_prefixes": ["QA", "QB", "QC", "QD", "QE", "QF", "QG", "QH"],
                          "code_year": 27, "code_months": [1, 5, 10],
                          "name_template": "合成对象{LETTER}"},
            "id_shape": {"pattern": "^[0-9]{4}$", "prefix_len": 2,
                         "year_digit": "2", "seq_min": 1, "seq_max": 12},
        }
    return json.loads(p.read_text(encoding="utf-8"))


def stable_hash(s):
    """确定性哈希 —— **不能用 Python 内置 hash()**(每进程随机, 不可复跑)。"""
    h = 0
    for ch in str(s):
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


class SyntheticConfig:
    """合成池配置。

    prefixes 绝不能撞 real_code_prefixes(那是真实世界存在的短前缀)。
    词表全部来自领域配置, 本类不内置任何领域知识。
    """

    def __init__(self, prefixes=None, year=27, months=(1, 5, 10),
                 name_template="合成对象{LETTER}", real_prefixes=None,
                 real_names=None, org_map=None, id_shape=None, domain=None):
        self.prefixes = list(prefixes or ["QA", "QB", "QC", "QD", "QE", "QF", "QG", "QH"])
        self.year = year
        self.months = list(months)
        self.name_template = name_template
        self.real_prefixes = list(real_prefixes or [])
        self.real_names = list(real_names or [])
        self.org_map = dict(org_map or {})
        self.id_shape = dict(id_shape or {
            "pattern": "^[0-9]{4}$", "prefix_len": 2,
            "year_digit": "2", "seq_min": 1, "seq_max": 12,
        })
        self.domain = domain

        bad = [p for p in self.prefixes if p.lower() in self.real_prefixes]
        if bad:
            raise ValueError(
                f"合成前缀 {bad} 撞上真实前缀 → 会被判定脚本当成真数据误报。"
                f"换一个(如 QA~QH)。见 references/pitfalls.md §5"
            )

    @classmethod
    def from_domain(cls, domain_cfg, **over):
        """从领域配置构造。over 里显式给的字段优先。"""
        d = domain_cfg or {}
        syn = d.get("synthetic", {})
        shape = d.get("id_shape", {})
        kw = {
            "prefixes": syn.get("code_prefixes"),
            "year": syn.get("code_year"),
            "months": syn.get("code_months"),
            "name_template": syn.get("name_template"),
            "real_prefixes": d.get("real_code_prefixes"),
            "real_names": d.get("real_display_names"),
            "org_map": d.get("org_map"),
            "id_shape": shape,
            "domain": d.get("display_name"),
        }
        kw = {k: v for k, v in kw.items() if v is not None}
        kw.update(over)
        return cls(**kw)

    def name(self, idx):
        return self.name_template.replace("{LETTER}", chr(ord("A") + idx))

    def code(self, idx, token):
        m = self.months[stable_hash(token) % len(self.months)]
        return f"{self.prefixes[idx].lower()}{self.year:02d}{m:02d}"


# ── 标识符形状守卫 ────────────────────────────────────────────────
def is_id_shape(digits, shape=None):
    """标识符里的数字部分是否是这个 app 的「编号形状」。

    默认形状: 4 位数 = 2 位年 + 01~12 序号(如 2701)。

    🚨 没有这个守卫, 一句普通文本里的 4 位数会被当成编号 → 整段文案被吃掉
    → 变成「最长合成对象C10+ 项」。见 §13/§20。
    """
    shape = shape or {}
    pat = shape.get("pattern", "^[0-9]{4}$")
    if not re.match(pat, str(digits)):
        return False
    s = str(digits)
    year_digit = shape.get("year_digit", "2")
    seq_min = int(shape.get("seq_min", 1))
    seq_max = int(shape.get("seq_max", 12))
    try:
        seq = int(s[2:])
    except ValueError:
        return False
    if len(s) >= 2 and s[0] == year_digit:
        return seq_min <= seq <= seq_max
    return False


def is_year_like(digits):
    """形如 2024 的 4 位数 —— 是年份不是编号, 不许动。"""
    try:
        return 1900 <= int(digits) <= 2099
    except Exception:
        return False


def build_sym_regex(prefixes, shape=None):
    """编号 token 正则。边界用 (?<![A-Za-z0-9]) 而不是 \\b —— 中文算 \\w, \\b 会错位。

    🚨 坑: 前缀列表里如果含**单字母**, 就会误吃普通英文单词:
       「最长 7000+ 项」前面若是中文+单字母, 会被拼成编号。
       → 判定/替换都要求 **编号形状**, 且单字母前缀必须紧贴数字。
    """
    shape = shape or {}
    plen = int(shape.get("prefix_len", 2))
    if not prefixes:
        return re.compile(r"(?!x)x")  # 永不匹配
    pat = "|".join(sorted(set(prefixes), key=len, reverse=True))
    dpat = shape.get("pattern", "^[0-9]{4}$").strip("^$")
    if plen <= 1:
        pref = r"[A-Za-z]"
    else:
        pref = r"[A-Za-z]{1,%d}" % plen
    return re.compile(r"(?<![A-Za-z0-9])(" + pat + r")[ ]?(" + dpat + r")(?![A-Za-z0-9])", re.I)


def _name_has_id_suffix(text_after_name, shape=None):
    """名字后面跟的数字是不是编号形状。

    「示例商品甲2701」→ 编号 → 整 token 一起替换
    「最长示例商品甲 7000+ 项」→ 7000 不是编号形状 → **只换名字, 不动数字**
    """
    shape = shape or {}
    plen = int(shape.get("prefix_len", 2))
    m = re.match(r"^\s*(?:[A-Za-z]{1,%d}[ ]?)?(\d+)" % plen, text_after_name or "")
    if not m:
        return False
    return is_id_shape(m.group(1), shape)


def scrub_js(src, cfg=None):
    """L1: 改 JS 源码里的硬编码字面量。返回清洗后的源码。"""
    cfg = cfg or SyntheticConfig()
    shape = cfg.id_shape
    out = src

    for real, fake in cfg.org_map.items():
        out = out.replace(real, fake)

    # 真实名称(+ 可选编号字母前缀 + 数字) → 占位符
    names = sorted(cfg.real_names, key=len, reverse=True)
    idx_map = {n: stable_hash(n) % len(cfg.prefixes) for n in names}

    for name in names:
        i = idx_map[name]
        # 🚨 只在后面的数字是**编号形状**时才吃整个 token。
        #    否则「最长示例商品甲 7000+ 项」里的 7000 会被当编号 → 整句被改坏。
        out = re.sub(
            re.escape(name) + r"([ ]?(?:[A-Za-z]{1,2}[ ]?)?)(\d{4})",
            lambda m, i=i: (f"\x01{i}\x01{cfg.months[stable_hash(m.group(2)) % len(cfg.months)]:02d}"
                            if is_id_shape(m.group(2), shape) else m.group(0)),
            out,
        )
        out = out.replace(name, f"\x01{i}\x01")

    # 编号 token → 合成编号
    rex = build_sym_regex(cfg.real_prefixes, shape)

    def _sub(m):
        if is_year_like(m.group(2)):
            return m.group(0)
        # 🚨 hex 颜色守卫: `#ef4444` / `#22c55e` 是 CSS 颜色, 不是编号。
        #    前端 bundle 里满地都是 → 不守会把配色全改坏, 画面直接崩。
        #    判据: `#` 紧贴 3/6 位 hex。
        start = m.start()
        if start > 0 and out[start - 1] == "#":
            return m.group(0)
        p = m.group(1).lower()
        idx = stable_hash(p) % len(cfg.prefixes)
        return cfg.code(idx, m.group(0))

    out = rex.sub(_sub, out)

    # 占位符 → 合成名
    out = re.sub(r"\x01(\d)\x01", lambda m: cfg.name(int(m.group(1))), out)
    return out


# ── L3: 业务数据字段级兜底 ─────────────────────────────────────
SYM_KEYS = {"symbol", "contract_symbol", "master", "secondary", "product_code",
            "contract", "code", "instrument", "sku", "item_code"}
NAME_KEYS = {"symbol_name", "contract_name", "name", "exchange", "product_name",
             "note", "remark", "title", "label", "description"}

RE_ANY_SYM = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,2})[ ]?(\d{4})(?![0-9])")


class BusinessScrubber:
    """业务接口响应递归清洗。

    🚨 free-text 字段(note/remark/description)最容易漏 ——
    编号替换后, 真实名称还留在自由文本里(实测漏过一次)。
    所以: 字符串先按编号形状替换, 再替换名称。
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or SyntheticConfig()

    def map_symbol(self, s):
        cfg = self.cfg
        m = re.match(r"^([A-Za-z]{1,2})(\d{4})$", str(s).strip())
        if not m:
            return s
        p, d = m.group(1).lower(), m.group(2)
        if p not in cfg.real_prefixes or not is_id_shape(d, cfg.id_shape):
            return s
        return cfg.code(stable_hash(p) % len(cfg.prefixes), str(s).strip())

    def map_text(self, s):
        cfg = self.cfg
        if not isinstance(s, str):
            return s
        for real in sorted(cfg.real_names, key=len, reverse=True):
            if real in s:
                s = s.replace(real, cfg.name(stable_hash(real) % len(cfg.prefixes)))
        for real, fake in cfg.org_map.items():
            s = s.replace(real, fake)
        return s

    def scrub(self, payload):
        def walk(o):
            if isinstance(o, dict):
                out = {}
                for k, v in o.items():
                    kl = str(k).lower()
                    if kl in SYM_KEYS and isinstance(v, str):
                        out[k] = self.map_symbol(v)
                    elif kl in NAME_KEYS and isinstance(v, str):
                        out[k] = self.map_text(v)
                    else:
                        out[k] = walk(v)
                return out
            if isinstance(o, list):
                return [walk(x) for x in o]
            if isinstance(o, str):
                o = RE_ANY_SYM.sub(lambda m: self.map_symbol(m.group(0).replace(" ", "")), o)
                return self.map_text(o)
            return o
        return walk(payload)


def self_check(cfg=None):
    """自检: 清洗结果里不得再出现真实前缀+数字 / 真实名称。

    🚨 别肉眼数反斜杠 / 别只看「跑通了」—— 实测清洗层空转过一次:
       JS 源码里 `\\\\d` 变成匹配字面反斜杠 → 编号清洗从来没生效, 但整条链路看起来是通的。
    """
    if cfg is None:
        # 自检需要一个非空词表才测得出东西 → 用内置测试域
        # 前缀含 "ef" 是故意的: 用来测 hex 颜色守卫(`#ef4444` 不能被吃掉)
        cfg = SyntheticConfig(
            real_prefixes=["ab", "cd", "ef"],
            real_names=["示例商品甲", "示例商品乙", "示例客户丙"],
            org_map={"示例仓库A": "合成仓库A"},
        )
    samples = [
        'const d="ab2701";const n="示例商品甲";',
        '如 cd2605 / 示例商品甲',
        'AB2610 · 0 项',
        '示例商品甲2610 示例商品乙2609 示例客户丙2701',
        'MA20 均线 MA2024 忽略',
        '最长示例商品甲 7000+ 项',
        '#ef4444 #22c55e rgba(0,0,0,.5)',
    ]
    blob = "\n".join(samples)
    out = scrub_js(blob, cfg)
    rex = build_sym_regex(cfg.real_prefixes, cfg.id_shape)
    # 🚨 判定也必须带编号形状守卫 —— 否则单字母+4位非编号会被误报成残留
    bad = [c for c in rex.findall(out)
           if not is_year_like(c[1]) and is_id_shape(c[1], cfg.id_shape)]
    names = [n for n in cfg.real_names + list(cfg.org_map) if n in out]
    print("原文:\n ", blob)
    print("替换后:\n ", out)
    print("残留真实编号:", bad if bad else "无 ✅")
    print("残留真实名称:", names if names else "无 ✅")
    if "7000+ 项" not in out:
        print("⚠️  数量被误吃 —— 编号形状守卫失效")
        return False
    if "7000+ 项" not in out or "项" not in out:
        return False
    # 🚨 hex 颜色守卫自检: CSS 颜色不能被当成编号吃掉(前端 bundle 满是 hex)
    for hexlit in ("#ef4444", "#22c55e"):
        if hexlit not in out:
            print(f"⚠️  hex 颜色 {hexlit} 被误吃 —— hex 守卫失效")
            return False
    return not bad and not names


if __name__ == "__main__":
    ok = self_check()
    print("\nself_check:", "PASS ✅" if ok else "FAIL ❌")
    raise SystemExit(0 if ok else 1)
