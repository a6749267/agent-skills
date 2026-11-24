#!/usr/bin/env python3
"""自检 —— 管线核心不变量的回归测试。

为什么需要这个:
    这套管线的产出是**视频**, 出错时不会抛异常, 而是画面里出现
    「顶栏汇总 ≠ 明细之和」这种**看起来还像那么回事**的假数据。
    肉眼很难发现, 但观众一眼看穿。

    所以把「必须成立的不变量」写成断言, 每次改动后跑一遍。

用法:
    python selfcheck.py            # 跑全部检查
    python selfcheck.py --verbose  # 打印细节

退出码: 0 = 全过, 1 = 有失败
"""
import argparse
import importlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

RESULTS = []


def check(name):
    """装饰器: 把一个函数注册成检查项。"""
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


# ── 1. agentenv: 路径解析 ───────────────────────────────────────
@check("agentenv 能定位真实家目录(不被沙箱 HOME 劫持)")
def _t_agentenv_home():
    import agentenv
    rh = pathlib.Path(agentenv.real_home())
    assert rh.is_dir(), f"real_home 指向不存在的目录: {rh}"
    assert rh != pathlib.Path("/"), "real_home 返回了根目录 —— 解析失败"
    # 沙箱 home 一般形如 <真实家目录>/.<框架>/home
    assert rh.name != "home" or rh.parent != rh, "real_home 仍是沙箱 home"
    return f"real_home = {rh}"


@check("agentenv 能定位 skill 根目录")
def _t_agentenv_skill():
    import agentenv
    root = agentenv.skill_root(skill_name="tutorial-video-recorder")
    assert (root / "SKILL.md").exists(), f"skill 根定位错: {root}"
    return f"skill_root = {root}"


@check("agentenv 解释器探测能选出有 playwright 的那个")
def _t_agentenv_python():
    import agentenv
    py = agentenv.pick_python("playwright")
    rc = subprocess.run([py, "-c", "import playwright"], capture_output=True)
    assert rc.returncode == 0, f"选出的解释器没有 playwright: {py}"
    return f"pick_python = {py}"


@check("凭据查找链非空(不要求一定有 key)")
def _t_agentenv_env():
    import agentenv
    cands = agentenv.env_file_candidates()
    assert cands, "候选链为空"
    existing = [c for c in cands if pathlib.Path(c).exists()]
    return f"候选 {len(cands)} 个, 其中 {len(existing)} 个存在"


# ── 2. synthetic: 合成数据守卫 ─────────────────────────────────
@check("synthetic 自检通过(无残留真实编号/名称)")
def _t_synthetic():
    r = subprocess.run([sys.executable, str(HERE / "synthetic.py"), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"synthetic 自检失败:\n{r.stdout}\n{r.stderr}"
    assert "PASS" in r.stdout, "synthetic 自检未报告 PASS"
    return "self_check PASS"


@check("hex 颜色不被当成编号改写")
def _t_hex_guard():
    import synthetic
    scfg = synthetic.SyntheticConfig(prefixes=None, year=27, months=(1,),
                                     name_template="合成对象{LETTER}")
    src = 'color: #ef4444; background: #22c55e; rgba(0,0,0,.5);'
    out = synthetic.scrub_js(src, scfg)
    assert "#ef4444" in out, f"hex 颜色被破坏: {out!r}"
    assert "#22c55e" in out, f"hex 颜色被破坏: {out!r}"
    assert "rgba(0,0,0,.5)" in out, f"rgba 被破坏: {out!r}"
    return "hex / rgba 颜色保持原样"


# ── 3. adapter: 汇总自洽(最关键) ─────────────────────────────
def _load_adapter():
    mod = importlib.import_module("adapters.ecommerce_demo.adapter")
    import tempfile
    a = mod.Adapter(
        {"domain": "ecommerce", "app": {"base_url": "http://example.test"},
         "synthetic": {}},
        pathlib.Path(tempfile.mkdtemp()),
    )
    return a


@check("adapter: 明细净额之和 == fix_summary 算出的 total_amount")
def _t_adapter_consistency():
    a = _load_adapter()
    orders = a._orders()
    # 用与 fix_summary 完全相同的语义独立算一遍
    expect = 0.0
    for o in orders:
        sign = 1.0 if o.get("side") == "sell" else -1.0
        expect += sign * float(o["unit_price"]) * float(o["quantity"])
    got = a.fix_summary("/api/account", {"total_credit": 100000.0}, None)
    total = float(got["total_amount"])
    assert abs(total - expect) < 0.02, (
        f"汇总与明细不一致: fix_summary={total}, 明细之和={round(expect,2)}, "
        f"差 {round(total-expect,2)}")
    return f"total_amount = {total} == 明细之和 {round(expect,2)}"


@check("adapter: 单价反解后净额精确等于目标值")
def _t_adapter_reverse():
    a = _load_adapter()
    orders = a._orders()
    targets = [1200.00, -1260.40, 1490.30]
    for o, target in zip(orders, targets):
        sign = 1.0 if o.get("side") == "sell" else -1.0
        net = sign * float(o["unit_price"]) * float(o["quantity"])
        assert abs(net - target) < 0.02, (
            f"{o['symbol']} 净额 {round(net,2)} != 目标 {target}")
    return "三笔净额全部精确命中"


@check("adapter: route 只命中业务路径, 其他放行")
def _t_adapter_route():
    a = _load_adapter()
    assert a.route("http://example.test/api/market/quotes") is not None, "未命中 quotes"
    assert a.route("http://example.test/api/market/items") is not None, "未命中 items"
    assert a.route("http://example.test/api/other/thing") is None, "不该命中"
    assert a.route("http://example.test/api/market/quotes?t=1") is not None, "带 query 未命中"
    return "命中/放行判定正确"


@check("adapter: 合成响应结构稳定(同一 symbol 永远同价)")
def _t_adapter_determinism():
    a = _load_adapter()
    q1 = a.quotes()
    q2 = a.quotes()
    assert q1 == q2, "两次调用结果不同 → 录屏前后画面会不一致"
    return f"{len(q1)} 条报价完全一致"


# ── 4. 领域配置 ────────────────────────────────────────────────
@check("领域配置文件合法且字段齐全")
def _t_domain_cfg():
    domains = HERE.parent / "assets" / "domains"
    assert domains.is_dir(), f"缺目录: {domains}"
    names = sorted(p.stem for p in domains.glob("*.json"))
    assert names, "没有任何领域配置"
    for p in domains.glob("*.json"):
        cfg = json.loads(p.read_text())
        for key in ("real_code_prefixes", "real_display_names", "org_map"):
            assert key in cfg, f"{p.name} 缺字段 {key}"
        assert "id_shape" in cfg, f"{p.name} 缺 id_shape"
    return f"{len(names)} 个领域配置: {', '.join(names)}"


@check("project.json 模板可解析且指向存在的领域")
def _t_project_tpl():
    tpl = HERE.parent / "assets" / "templates" / "project.json"
    assert tpl.exists(), f"缺模板: {tpl}"
    cfg = json.loads(tpl.read_text())
    dom = cfg.get("domain")
    assert dom, "模板没写 domain"
    dpath = HERE.parent / "assets" / "domains" / f"{dom}.json"
    assert dpath.exists(), f"模板指向不存在的领域配置: {dom}"
    return f"domain={dom} → {dpath.name} 存在"


# ── 5. 扫描器 ──────────────────────────────────────────────────
@check("合规扫描器可运行(位置参数用法)")
def _t_compliance():
    # 用法: python compliance_scan.py <文件或目录> --domain default
    r = subprocess.run(
        [sys.executable, str(HERE / "compliance_scan.py"), str(HERE.parent),
         "--domain", "default"],
        capture_output=True, text=True)
    # 退出码 0=无 STRONG 命中, 1=有命中。两种都算「能跑」, 但不能是崩溃(2+)
    assert r.returncode in (0, 1), (
        f"compliance_scan 异常退出 ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return f"退出码 {r.returncode} (0=干净 / 1=有 STRONG 命中)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("=== 管线自检 ===\n")
    passed = failed = 0
    for name, fn in RESULTS:
        try:
            detail = fn()
            print(f"  ✅ {name}")
            if args.verbose and detail:
                print(f"       {detail}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {name}")
            print(f"       {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} 项通过, {failed} 项失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
