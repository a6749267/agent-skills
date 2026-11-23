#!/usr/bin/env python3
"""真录屏 —— Playwright 录制真实应用, 全程合成数据。

用法:
    python recorder.py --config project.json [--only 0,3] [--segments-dir raw]

每段独立录制(新 profile), 段首插**黑场标记**用于像素级音画对齐。
录制用的是**真前端 + 真路由 + 合成响应**, 不是拼图。

🚨 关键约束:
  1. **必须用完整 chromium**, 不是 headless_shell —— 后者不支持 record_video 的 screencast。
  2. **每段一个独立 context**: 段首状态干净, 不会互相污染。
  3. **登录态用 localStorage 注入 token**, 不要每段走登录表单 ——
     前端表单在无头环境下**偶发不提交**(连请求都不发, 静默失败)。
  4. **浏览器磁盘缓存会让拦截完全失效** → 加 --disable-http-cache。
  5. 拦截逻辑**只能有一份**, 录制和验收共用 → 见 interceptor.py。
  6. **app 相关的路径/缓存键全在 project.json 里** —— 本文件不含任何 app 专属信息。
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from playwright.sync_api import sync_playwright  # noqa: E402

import synthetic  # noqa: E402
from interceptor import make_handler  # noqa: E402


def find_chromium():
    """找完整 chromium(带 chrome-mac*/Google Chrome for Testing.app)。"""
    import os
    cands = []
    pb = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if pb:
        cands.append(pathlib.Path(pb))
    cands += [
        pathlib.Path.home() / "Library/Caches/ms-playwright",
        pathlib.Path.home() / ".cache/ms-playwright",
    ]
    # HERMES_HOME 下的缓存(playwright 装在 hermes home 里时)
    hh = os.environ.get("HERMES_HOME")
    if hh:
        cands.append(pathlib.Path(hh) / "home/Library/Caches/ms-playwright")
        cands.append(pathlib.Path(hh) / "Library/Caches/ms-playwright")
    for base in cands:
        if not base.exists():
            continue
        for d in sorted(base.glob("chromium-*")):
            for exe in d.glob("**/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"):
                return str(exe)
            for exe in d.glob("**/chrome"):
                return str(exe)
            for exe in d.glob("**/chrome-linux/chrome"):
                return str(exe)
    return None  # 交给 playwright 自己找


BLACK_JS = """() => {
  let d = document.getElementById('__blk');
  if (!d) {
    d = document.createElement('div');
    d.id = '__blk';
    d.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;background:#000;z-index:2147483647';
    document.body.appendChild(d);
  }
}"""
UNBLACK_JS = """() => { const d=document.getElementById('__blk'); if(d) d.remove(); }"""


def seg_duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--segments-dir", default=None)
    ap.add_argument("--only", default=None, help="只录这几段, 如 0,3,7")
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())
    work = pathlib.Path(cfg["paths"]["work_dir"])
    raw = pathlib.Path(a.segments_dir) if a.segments_dir else work / cfg["paths"].get("raw_dir", "raw")
    raw.mkdir(parents=True, exist_ok=True)

    exe = find_chromium()
    if not exe:
        print("❌ 找不到完整 chromium —— 跑 playwright install chromium", file=sys.stderr)
        return 2
    print(f"chromium: {exe}")

    base = cfg["app"]["base_url"].rstrip("/")
    vw = cfg["video"]["width"]
    vh = cfg["video"]["height"]
    scfg = build_synthetic_config(cfg)
    # 数据层: 由 adapter 提供(见 adapters/README)
    adapter = load_adapter(cfg, work)

    segments = cfg["segments"]
    want = set(int(x) for x in a.only.split(",")) if a.only else None

    # 清旧产物 —— 避免陈旧文件污染下游(见 pitfalls §38)
    for f in raw.glob("*.webm"):
        f.unlink()

    tokens = load_tokens(work)
    log = []
    with sync_playwright() as p:
        for idx, seg in enumerate(segments):
            if want is not None and idx not in want:
                continue
            route = seg.get("route", "/")
            print(f"\n[{idx}] {seg.get('module','')} {seg.get('title','')} → {route}")

            ctx = p.chromium.launch_persistent_context(
                user_data_dir=f"/tmp/pw_rec_{idx}_{int(time.time())}",
                executable_path=exe,
                headless=not a.headful,
                viewport={"width": vw, "height": vh},
                record_video_dir=str(raw),
                record_video_size={"width": vw, "height": vh},
                args=["--enable-unsafe-swiftshader", "--force-color-profile=srgb",
                      "--hide-scrollbars", "--mute-audio",
                      "--disable-http-cache", "--disk-cache-size=1"],
            )
            ctx.route("**/*", make_handler(cfg=cfg, scfg=scfg, adapter=adapter, work=work))
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()

            if tokens:
                pg.add_init_script("""
                try {
                  localStorage.setItem('access_token', %s);
                  localStorage.setItem('refresh_token', %s);
                } catch(e) {}
                """ % (json.dumps(tokens[0]), json.dumps(tokens[1])))
            # 屏蔽前端缓存键, 否则会命中上一轮的旧数据
            # 🚨 键名**不写死** —— 每个 app 的缓存键不一样, 写死 = 绑死某个 app。
            #    放在 project.json 的 `storage_keys_block`。
            blocked = json.dumps(cfg.get("storage_keys_block") or [])
            pg.add_init_script("""
            (() => {
              const BLOCKED = %s;
              const os_ = Storage.prototype.setItem, og_ = Storage.prototype.getItem;
              Storage.prototype.setItem = function(k,v){
                if(typeof k==='string' && BLOCKED.some(b => k===b || k.startsWith(b)))return;
                return os_.call(this,k,v);
              };
              Storage.prototype.getItem = function(k){
                if(typeof k==='string' && BLOCKED.some(b => k===b || k.startsWith(b)))return null;
                return og_.call(this,k);
              };
            })();
            """ % blocked)

            t0 = time.monotonic()
            pg.goto(base + route, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(5500)

            if "/login" in pg.url:
                print(f"  [{idx}] 登录态失效 → 回退登录流程")
                do_login(pg, cfg)
                pg.evaluate("""(p)=>{
                    if(window.next&&window.next.router){window.next.router.push(p);return}
                    history.pushState({},'',p);window.dispatchEvent(new PopStateEvent('popstate'));
                }""", route)
                pg.wait_for_timeout(5000)

            # DOM 兜底清洗(bundle 层已处理大部分, 这里只做最后一道)
            # 🚨 这层必须有**真实作用** —— 历史上这里是空函数(注释说兜底但代码是空的),
            #    属于"看起来有兜底其实没有"。现在按配置里的真实名称表真做替换。
            try:
                names = json.dumps(list(scfg.real_names))
                orgs = json.dumps(scfg.org_map)
                pg.evaluate(DOM_SANITIZE, {"names": names, "orgs": orgs})
            except Exception:
                pass
            pg.wait_for_timeout(600)

            # 黑场标记
            pg.evaluate(BLACK_JS)
            pg.wait_for_timeout(2000)
            pg.evaluate(UNBLACK_JS)
            pg.wait_for_timeout(400)

            act = seg.get("action", "hold")
            if act.startswith("scroll:"):
                y = int(act.split(":")[1])
                pg.evaluate(f"window.scrollTo({{top:{y},behavior:'smooth'}})")
                pg.wait_for_timeout(1800)

            # 保持到足够长(覆盖旁白)。旁白时长 = 该段 wav/mp3 时长
            need = seg.get("_dur", 30.0)
            end = t0 + 12.0 + need
            while time.monotonic() < end:
                pg.mouse.move(vw // 2 + (time.monotonic() * 37 % 100) - 50,
                              vh // 2 + (time.monotonic() * 23 % 50) - 25)
                pg.wait_for_timeout(150)

            ctx.close()
            vids = sorted(raw.glob("*.webm"), key=lambda x: x.stat().st_mtime)
            vf = vids[-1] if vids else None
            if vf:
                dst = raw / f"seg{idx:02d}.webm"
                if dst.exists():
                    dst.unlink()
                vf.rename(dst)
                print(f"  → {dst.name} {dst.stat().st_size//1024}KB  {seg_duration(dst):.2f}s")
            else:
                print("  ❌ 没录到视频")
            log.append({"idx": idx, "module": seg.get("module"), "route": route,
                        "title": seg.get("title"), "file": vf.name if vf else None})

        (raw / "log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
    print("\n=== 录制完成 ===")
    for f in sorted(raw.glob("seg*.webm")):
        print(f"  {f.name} {f.stat().st_size//1024}KB {seg_duration(f):.2f}s")
    return 0


DOM_SANITIZE = """(arg) => {
  const NAMES = arg && arg.names ? JSON.parse(arg.names) : [];
  const ORGS  = arg && arg.orgs  ? JSON.parse(arg.orgs)  : {};
  if (!NAMES.length && !Object.keys(ORGS).length) return 0;
  let hits = 0;
  const walk = (n) => {
    if (n.nodeType === 3) {
      let t = n.nodeValue;
      if (t) {
        for (const nm of NAMES) {
          if (t.indexOf(nm) >= 0) { t = t.split(nm).join('合成对象'); hits++; }
        }
        for (const k of Object.keys(ORGS)) {
          if (t.indexOf(k) >= 0) { t = t.split(k).join(ORGS[k]); hits++; }
        }
        n.nodeValue = t;
      }
    }
    for (const c of (n.childNodes||[])) walk(c);
  };
  try { if (document.body) walk(document.body); } catch(e) {}
  return hits;
}"""


def do_login(pg, cfg):
    app = cfg["app"]
    pg.goto(cfg["app"]["base_url"].rstrip("/") + app.get("login_path", "/login"),
            wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(2500)
    try:
        pg.fill("#username", app["login_username"])
        pg.fill("#password", app["login_password"])
    except Exception as e:
        print("  ⚠️  登录表单填失败:", e)
        return
    btn = app.get("login_mode_button")
    if btn:
        try:
            pg.get_by_role("button", name=btn, exact=True).first.click(timeout=4000)
        except Exception:
            pass
    pg.wait_for_timeout(300)
    try:
        pg.click("button[type=submit]")
    except Exception as e:
        print("  ⚠️  提交按钮点击失败:", e)
    pg.wait_for_timeout(5500)


def load_tokens(work):
    a = work / "token.txt"
    b = work / "token2.txt"
    if a.exists():
        t = a.read_text().strip()
        r = b.read_text().strip() if b.exists() else t
        return (t, r)
    return None


def build_synthetic_config(cfg):
    """从 project.json 构造合成配置。

    领域词表来自 `domain` 指定的 assets/domains/<name>.json;
    project.json 里的 `synthetic` 段可以覆盖合成侧参数(前缀/年份/模板)。
    """
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
    # 覆盖后重新校验前缀冲突
    bad = [p for p in scfg.prefixes if p.lower() in scfg.real_prefixes]
    if bad:
        raise ValueError(f"合成前缀 {bad} 撞上真实前缀 → 见 pitfalls §5")
    return scfg


def load_adapter(cfg, work):
    """加载 adapter(可选)。没有就返回 None —— 只有行情接口需要。"""
    name = cfg.get("adapter")
    if not name:
        return None
    p = HERE / "adapters" / name
    if not p.exists():
        print(f"⚠️  adapter {name} 不存在, 跳过")
        return None
    sys.path.insert(0, str(p))
    import importlib
    return importlib.import_module("adapter").Adapter(cfg, work)


if __name__ == "__main__":
    raise SystemExit(main())
