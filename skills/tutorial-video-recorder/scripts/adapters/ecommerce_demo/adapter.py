#!/usr/bin/env python3
"""Adapter 模板 —— 接自己的 app 时**只需要改这一个文件**。

Adapter 负责两件事:
  1. `route(url)` → 数据类接口的**整个响应替换**成合成 payload(L2)
  2. `fix_summary(url, payload, scfg)` → 业务接口的**汇总字段重算**(L3, 可选)

为什么要 adapter:
  - 合成数据的**结构必须和真响应一模一样**, 否则前端渲染不出来
    (字段名/嵌套/数组长度/枚举值 —— 一个字段对不上就是「暂无」空态)
  - 每个 app 的数据字段完全不同 → 不能写死在通用层

照着这个模板改:
  1. 先**探明**真响应的结构(见 references/adapter-guide.md §1)
  2. 只替换**值**, 保留**结构**
  3. 用同一套 synthetic 生成数据 —— 否则顶栏汇总和明细对不上

本例用一个**完全虚构**的电商订单后台演示, 不指向任何真实系统。
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

import synthetic  # noqa: E402


class Adapter:
    """示例实现: 电商订单后台。换成别的 app 时替换以下方法。"""

    def __init__(self, cfg, work):
        self.cfg = cfg
        self.work = pathlib.Path(work)
        scfg = cfg.get("synthetic", {})
        self.scfg = synthetic.SyntheticConfig(
            prefixes=scfg.get("code_prefixes"),
            year=scfg.get("code_year", 27),
            months=scfg.get("code_months", (1, 5, 10)),
            name_template=scfg.get("name_template", "合成对象{LETTER}"),
        )
        # 合成商品池 —— 必须与前端 /api/items 里能查到的记录一致,
        # 否则前端查不到 → 显示 '--'
        self.items = [
            {"code": "QA", "name": "合成对象A"},
            {"code": "QB", "name": "合成对象B"},
            {"code": "QC", "name": "合成对象C"},
            {"code": "QD", "name": "合成对象D"},
            {"code": "QE", "name": "合成对象E"},
            {"code": "QF", "name": "合成对象F"},
            {"code": "QG", "name": "合成对象G"},
            {"code": "QH", "name": "合成对象H"},
        ]
        self.months = [1, 5, 10]

    # ── 合成数据 ────────────────────────────────────────────────
    def symbols(self):
        out = []
        for i, p in enumerate(self.items):
            for m in self.months:
                out.append(f"{p['code'].lower()}{self.scfg.year:02d}{m:02d}")
        return out

    def quotes(self, symbols=None):
        """确定性生成数据。**同一 symbol 永远给同一个值** —— 否则录屏前后不一致。"""
        symbols = symbols or self.symbols()
        out = []
        for s in symbols:
            rr = synthetic.stable_hash(s)
            base = 100 + (rr % 900)
            drift = ((rr // 7) % 100 - 50) / 100.0
            last = round(base * (1 + drift / 100.0), 2)
            prev = round(base, 2)
            chg = round(last - prev, 2)
            pct = round(chg / prev * 100, 2) if prev else 0.0
            out.append({
                "symbol": s,
                "unit_price": last,
                "change": chg,
                "change_pct": pct,
                "list_price": round(prev * 1.001, 2),
                "high_price": round(last * 1.004, 2),
                "low_price": round(last * 0.996, 2),
                "prev_price": prev,
                "stock": int(6000 * (0.6 + (rr % 90) / 100.0)),
                "sold_count": int(1400 * (0.98 + (rr % 5) / 100.0)),
                "source": "synthetic",
                "tick_time": "150000",
                "trade_date": "2026-01-01",
            })
        return out

    def quote_map(self, symbols=None):
        return {q["symbol"]: q for q in self.quotes(symbols)}

    # ── L2: 数据接口整体替换 ────────────────────────────────────
    def route(self, url):
        """返回 (status, body) 表示替换, None 表示不管。

        🚨 匹配要按**路径**, 不要按完整 URL(后面可能带 query)。
        🚨 合成响应的**结构**必须和真响应一模一样。
        """
        path = url.split("?")[0]

        if path.endswith("/api/market/quotes"):
            return 200, self._shape_quotes(self.quotes())

        if path.endswith("/api/market/items"):
            return 200, {"items": self._items()}

        return None

    def _items(self):
        items = []
        for p in self.items:
            for m in self.months:
                items.append({
                    "symbol": f"{p['code'].lower()}{self.scfg.year:02d}{m:02d}",
                    "name": f"{p['name']}{m:02d}",
                    "item_code": p["code"],
                    "warehouse": "合成仓库A",
                    "is_active": m == 1,
                })
        return items

    def _shape_quotes(self, quotes):
        """🚨 这里必须**照抄真响应的外层结构**。
        换 app 时: 先抓真响应(见 adapter-guide.md §1), 再照着拼。
        """
        return {"items": quotes, "source": "synthetic"}

    # ── L3: 汇总重算(可选) ──────────────────────────────────────
    def fix_summary(self, url, payload, scfg):
        """修「顶栏汇总与明细自相矛盾」。

        实测: 后端把 total_amount / today_count **硬编码 0**,
        而前端「订单概览」用单价自己算 →
        同一画面里顶栏 +0.00、明细 −1424.00, 观众一眼看穿。

        修法: 用**同一套数据**重算汇总字段, 不要另算一套价格。
        ⚠ 修完必须在浏览器里断言 API before/after 真的变了 —— 见 pitfalls §33。
        """
        if "/api/account" not in url:
            return payload
        try:
            orders = self._orders()
            total = 0.0
            count = 0
            for o in orders:
                q = self.quote_map([o["symbol"]]).get(o["symbol"], {})
                unit = float(q.get("unit_price") or o["unit_price"])
                qty = float(o["quantity"])
                total += unit * qty
                count += int(qty)
            payload["total_amount"] = round(total, 2)
            payload["today_count"] = count
            payload["available"] = round(float(payload.get("total_credit", 0)) - total, 2)
            eq = float(payload.get("total_credit", 0))
            payload["usage_rate"] = round(total / eq * 100, 2) if eq else 0.0
        except Exception:
            pass
        return payload

    def _orders(self):
        """示例订单明细。

        🚨 unit_price 必须和 quotes() 生成的 unit_price **自洽** ——
        否则 fix_summary 算出的金额会完全失真。
        (实测: 历史值抄的是 3180/5420/4080, 而 quotes() 生成的是 4418/5276/3094
              → 金额算出 +24760/-29557, 比例全错, 而"看起来还是有数字"。)

        正确做法: **反解** unit_price, 让金额等于你想要的数。
        ```
        last = qm[sym]["unit_price"]
        unit = last - target/qty      # 卖出
        unit = last + target/qty      # 买入
        ```
        这里取的 target 是 -1424 / +1280.40 / -1509.30, 合计 -1652.90,
        模拟「顶栏汇总 = 明细之和」的自洽画面。
        """
        qm = self.quote_map()
        spec = [
            # symbol,      side,    qty, 目标金额
            ("qa2701", "sell", 2, -1424.00),
            ("qc2701", "buy",  1,  1280.40),
            ("qe2701", "sell", 3, -1509.30),
        ]
        out = []
        for sym, side, qty, target in spec:
            last = float(qm[sym]["unit_price"])
            unit = last - target / qty if side == "sell" else last + target / qty
            out.append({"symbol": sym, "side": side, "quantity": qty,
                        "unit_price": round(unit, 2)})
        return out
