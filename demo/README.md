# Demo

一个**完全虚构**的合成订单后台，用来 30 秒看懂这套 skill 在干什么。

数据全部由 skill 自带的合成引擎确定性生成 —— 同一编号永远同一价格，
不含任何真实系统。

## 跑起来

```bash
python demo/serve.py
# → http://127.0.0.1:8899
```

改端口：

```bash
python demo/serve.py --port 9000
```

## 关键看点

**顶栏「订单净额」= 表格三笔金额之和**（`+1,848.66 − 156.00 + 453.00 = +2,145.66`）。

这就是这套 skill 的核心保证：**画面上的数字自洽**。
大多数脱敏工具做不到 —— 它们替换了明细，却没重算汇总，于是顶栏写 `+0.00`、
表格写 `−1200.00`，观众两秒就能看出矛盾。

想看这个数字怎么算出来的：

```bash
# 同一份数据，adapter 反解出单价让净额精确命中目标
python - <<'PY'
import sys, pathlib
sys.path.insert(0, "skills/tutorial-video-recorder/scripts")
from adapters.ecommerce_demo.adapter import Adapter
a = Adapter({"synthetic": {"code_prefixes": ["QA","QB","QC","QD","QE","QF","QG","QH"],
                           "code_year": 27, "code_months": [1,5,10]}},
            pathlib.Path("demo/.work"))
for o in a._orders():
    sign = 1.0 if o["side"] == "sell" else -1.0
    net = sign * o["unit_price"] * o["quantity"]
    print(f'{o["code"]}  {o["side"]}  x{o["quantity"]}  →  {net:>10.2f}')
PY
```

## 直接调 API

```bash
curl -s http://127.0.0.1:8899/api/market/quotes | python3 -m json.tool | head -20
curl -s http://127.0.0.1:8899/api/market/items  | python3 -m json.tool | head -20
```

返回的 payload 结构与真实后端一致（外层 `{"items": [...]}`），
这就是 adapter 的 `route()` 要做的事：**照抄真实响应的结构，只换值**。

## 用它录一段

`demo/serve.py` 起服务后，可以直接拿它当录制目标：

```bash
# 1) 起服务
python demo/serve.py &

# 2) 录一段（配置见 skills/tutorial-video-recorder/assets/templates/project.json）
python skills/tutorial-video-recorder/scripts/recorder.py --config project.json

# 3) 全片扫泄漏 —— 退出码 0 才算干净
python skills/tutorial-video-post/scripts/scan_leak.py final.mp4 --ocr --transcribe
```
