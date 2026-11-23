#!/usr/bin/env python3
"""拦截层 —— 录制和验收**共用同一份实现**。

🚨🚨 铁律: 拦截逻辑只写在这里一份。历史上出现过 verify 脚本 import 了清洗模块
   但 on_route 里根本没用 → 报「零泄漏」是侥幸, 两边结论会分叉。
   自查: 录制和验收都调 make_handler(), 而不是各写一份。

三层(从外到内):
  L1  JS bundle  →  scrub_js: 改前端硬编码的真实字面量(名称/编号)
  L2  数据类 API →  adapter.route(): 整个响应替换成合成 payload
  L3  业务类 API →  放行真实后端 + 字段级清洗 + 汇总重算(fixer)

API 路径匹配**按路径子串**, 不要按完整 URL —— 后面可能带 query string。

🚨 业务路径表**不写死在这里** —— 每个 app 的接口路径都不一样,
   写死 = 通用层被某个 app 绑死。放在 project.json 的 `business_paths`。
"""
import fnmatch
import json
import re
import sys

HERE = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import synthetic  # noqa: E402

# ── L1 JS bundle 路径 ────────────────────────────────────────────
BUNDLE_PATTERNS = (
    re.compile(r"/_next/static/.*\.js"),
    re.compile(r"/_next/static/chunks/.*\.js"),
    re.compile(r"/static/.*\.js"),
    re.compile(r"/assets/.*\.js"),
    re.compile(r"/_app/.*\.js"),
)

# ── 业务接口路径默认值 ───────────────────────────────────────────
# ⚠️ 这是**兜底默认值**, 只在你没填 project.json 的 `business_paths` 时用。
#    强烈建议在自己的 project.json 里覆盖 —— 这里只是保证「不配置也能跑」。
DEFAULT_BUSINESS_PATHS = (
    "/api/list",
    "/api/detail",
    "/api/orders",
    "/api/items",
    "/api/account",
    "/api/profile",
    "/api/settings",
    "/api/users/me",
    "/api/auth/me",
)


def _business_paths(cfg):
    """从 project.json 取业务路径表。支持 fnmatch 通配(如 /api/*/orders)。"""
    if cfg:
        pats = cfg.get("business_paths")
        if pats:
            return tuple(pats)
    return DEFAULT_BUSINESS_PATHS


def is_business(url, paths=None):
    paths = paths or DEFAULT_BUSINESS_PATHS
    path = url.split("?")[0]
    for p in paths:
        if "*" in p or "?" in p:
            if fnmatch.fnmatch(path, p):
                return True
        elif p in path:
            return True
    return False


def is_bundle(url):
    path = url.split("?")[0]
    return any(p.search(path) for p in BUNDLE_PATTERNS)


def make_handler(cfg=None, scfg=None, adapter=None, work=None,
                 verbose=False, on_business=None, on_market=None):
    """返回一个 Playwright route handler。

    参数:
      cfg     项目配置 dict(含 business_paths)
      scfg    synthetic.SyntheticConfig
      adapter 可选, 有 .route(url) -> (status, body) | None
      work    工作目录
      on_business 回调 (url, cleaned_payload) —— 用于断言修复真的生效
      on_market   回调 (url, body)
    """
    cfg = cfg or {}
    scfg = scfg or synthetic.SyntheticConfig()
    scrubber = synthetic.BusinessScrubber(scfg)
    bpaths = _business_paths(cfg)

    # 汇总重算器(可选): 修「顶栏汇总与明细自相矛盾」
    fixer = None
    if adapter is not None and hasattr(adapter, "fix_summary"):
        fixer = adapter.fix_summary

    def on_route(route):
        u = route.request.url

        # ── L1: JS bundle ──
        if is_bundle(u):
            try:
                resp = route.fetch()
                raw = resp.body().decode("utf-8", "replace")
                new = synthetic.scrub_js(raw, scfg)
                if verbose and new != raw:
                    print(f"  [scrub-js] {u.split('/')[-1][:44]} {len(raw)}->{len(new)}")
                route.fulfill(status=200,
                              content_type="application/javascript; charset=utf-8",
                              body=new.encode("utf-8"))
                return
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass
                return

        # ── L2/L3: API ──
        if "/api/" in u or adapter is not None:
            # L2: 数据类 → 整个替换
            if adapter is not None:
                try:
                    r = adapter.route(u)
                except Exception:
                    r = None
                if r:
                    status, body = r
                    if on_market:
                        on_market(u, body)
                    route.fulfill(status=status,
                                  content_type="application/json; charset=utf-8",
                                  body=json.dumps(body, ensure_ascii=False))
                    return

            # L3: 业务类 → 放行 + 清洗 + 汇总重算
            if is_business(u, bpaths):
                try:
                    resp = route.fetch()
                    data = json.loads(resp.body().decode("utf-8", "replace"))
                    clean = scrubber.scrub(data)
                    if fixer is not None:
                        clean = fixer(u, clean, scfg)
                    if on_business:
                        on_business(u, clean)
                    route.fulfill(status=200,
                                  content_type="application/json; charset=utf-8",
                                  body=json.dumps(clean, ensure_ascii=False))
                    return
                except Exception:
                    try:
                        route.continue_()
                    except Exception:
                        pass
                    return

        route.continue_()

    return on_route
