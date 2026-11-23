# Adapter 指南 —— 把管线接到**你的** app 上

管线(通用): 录制 → 合成 → 验收
Adapter(专属): 数据结构 + 汇总重算 + 业务数据

**大部分工作在这里。** 通用层不用改。

---

## 0. 先问清楚三个问题

| 问题 | 决定什么 |
|---|---|
| app 是自己写的还是第三方的？ | 自己的 → 可以直接改后端加"演示模式"；第三方的 → 只能网络层拦截 |
| 有后端数据库吗？ | 有 → 能灌业务数据（界面才"活"）；没有 → 只能靠 adapter 合成 |
| 观众要看到"有数据在用"的界面吗？ | 要 → 必须让业务接口有数据；不要 → 空态也能录，但会像"没登录" |

---

## 1. 探明真响应的结构（**第一步，不能跳**）

要拿到每个接口的**真响应结构**（不含真数据）。

```python
# grab_pure_api.py —— 不装任何 route 拦截, 只监听 response 落盘
from playwright.sync_api import sync_playwright
import json, pathlib

OUT = pathlib.Path("api_pure.json")
data = {}

with sync_playwright() as p:
    ctx = p.chromium.launch(headless=True)
    pg = ctx.new_page()
    pg.on("response", lambda r: _save(r))
    pg.goto(BASE + "/dashboard", wait_until="networkidle")
    ...
```

🚨 **必须单独跑一遍不注入任何合成层的抓取**。
否则抓到的"真 token"里混着你自己的合成响应 → 再搜回合成 payload 必然命中 → 报"泄漏"（自证循环）。

拿到之后，对每个接口问：
- 外层是 `{"items": [...]}` 还是 `[...]` 还是 `{"data": {...}}`？
- 字段名原样是什么（`symbol` / `contract_symbol` / `code`）？
- 枚举值有哪些（前端靠 `flowTypeLabel()` 之类的映射，**用前端不认的值 → 裸露英文**）？
- 数值字段哪些是**盈亏/权益类**（必须为 0 或合成），哪些是**价格类**（应该非零）？

🚨 **"所有金额必须为 0"是错的** —— 合成数据的单价/数量本来就该非零，
否则图表是空的。只有**盈亏/权益类**字段该为 0。判定要**按字段名**。

---

## 2. 写 adapter 的 route()

```python
def route(self, url):
    path = url.split("?")[0]          # ← 按路径匹配, 别按完整 URL
    if path.endswith("/api/market/quotes"):
        return 200, self._shape_quotes(self.quotes())
    return None                        # ← None = 不管, 走通用层
```

**结构必须一模一样。** 一个字段对不上 → 前端渲染不出来 → 画面显示"暂无"。

自检：
```python
# 对比真响应和合成响应的顶层 key 集合
assert set(real.keys()) == set(fake.keys())
# 数组元素也一样
assert set(real["items"][0].keys()) == set(fake["items"][0].keys())
```

---

## 3. 让业务界面"活"起来（灌数据）

新账号天然是空的 → 录出来像"没登录/像首页"。这是历史上被用户直接点破的问题。

**方案 A：灌数据库**（有后端时最真实）

```python
# 六个必踩的坑(详见 pitfalls §29):
# ① UUID 传 str()
# ② offset 是 SQL 保留字 → 加引号
# ③ 查 uuid 列用 user_id::text=%s
# ④ 同名对象跨盘唯一 → 加后缀
# ⑤ 占位符数量对不上 → 报错 "not all arguments converted" → 用具名参数
# ⑥ except: conn.rollback() 会把整个事务回滚 → 一条失败前面几百行全撤,
#    脚本却打印"成功" → 每步独立 commit, 错误直接抛
```

灌完库后**必须**：
1. 在拦截层放行这些业务接口（否则界面还是空的）
2. 加字段级兜底清洗（万一哪天库里混进真数据）
3. 逐页 `inner_text` 抓三件事：
   - 裸露的英文枚举
   - 编号与名称错位
   - 该有价格的地方是 `--`

**方案 B：adapter 合成**（无后端时）

在 `route()` 里返回合成的业务 payload。结构同样要照抄真响应。

---

## 4. 汇总重算（画面自洽）

只要画面上有"总额 + 明细"，就必须保证两者一致。

```python
def fix_summary(self, url, payload, scfg):
    if "/api/account" not in url:
        return payload
    # 用**同一套数据**重算, 别另算一套价格
    ...
```

🚨 单元测试通过 ≠ 路由生效。必须在浏览器里断言：

```python
def on_business(url, payload):
    if "/api/account" in url:
        print("顶栏汇总 =", payload.get("total_amount"))
        assert payload.get("total_amount") != 0, "汇总重算没生效!"
```

---

## 5. 合成前缀不能撞真实前缀

```python
scfg = synthetic.SyntheticConfig(prefixes=["QA","QB",...])
# 构造时就检查 —— 撞了直接抛异常
```

判定用的"真实前缀表"**以你的 app 为准**：
- 你的 app 用什么前缀体系, 就填什么
- 例: 电商 → sku/ord/cus; SaaS → org/proj/sub
- 加密 → btc/eth/...
- 自定义 app → 抓真响应统计出来

---

## 6. 合成数据要可复跑

```python
# ✅ 自己的稳定哈希
h = 0
for ch in s: h = (h*131 + ord(ch)) & 0xFFFFFFFF

# ❌ Python 内置 hash() —— 每进程随机 → 每次跑出来的数据都不一样
```

实测踩过：合成价格每次跑都变 → 录屏前后界面数字对不上。

---

## 7. 收工检查表

- [ ] 真响应结构已抓（纯净的，没注入合成层）
- [ ] adapter 的 route() 覆盖了所有数据接口
- [ ] 合成响应结构 = 真响应结构（逐 key 比对过）
- [ ] 业务界面有数据（灌库或合成）
- [ ] 顶栏汇总 = 明细之和（跑一遍 fix_summary）
- [ ] 合成前缀不撞真实前缀（构造时检查）
- [ ] 数字可复跑（稳定哈希）
- [ ] 逐页 inner_text 无裸露英文/无 `--`
