# Adapters —— 每个 app 一个目录

```
adapters/
└── futures_demo/
    └── adapter.py
```

**换 app 就新建一个目录，复制 futures_demo 改。**

## 接口约定

```python
class Adapter:
    def __init__(self, cfg, work):
        """cfg = project.json 内容, work = 工作目录"""

    def route(self, url):
        """L2: 数据类接口整体替换。
        返回 (status, body) 表示替换; 返回 None 表示不管, 交给通用层。
        """

    def fix_summary(self, url, payload, scfg):
        """L3: 汇总字段重算(可选)。
        返回清洗后的 payload。
        """
```

两个方法**都是可选的**：
- 只有 `route()` → 只有数据接口被合成，业务接口走真实后端
- 两个都有 → 数据 + 汇总都对

## futures_demo 里可以抄的东西

| 东西 | 为什么重要 |
|---|---|
| `stable_hash` | 确定性哈希 —— 合成数据可复跑 |
| `quotes()` | 生成数据的**形状**参考：字段名 / 嵌套 / 单位 |
| `_shape_quotes()` | 🚨 **合成响应的外层结构必须 = 真响应结构**，这里给的是对照写法 |
| `fix_summary()` | 顶栏汇总重算的写法 —— 用**同一套数据**算，别另算一套价格 |
| `_records()` | 示例明细。真做法是查数据库 |

## ⚠ 改之前必须做的三件事

1. **抓真响应的纯净结构** —— 不注入任何合成层，否则抓到的"真数据"里混着你的合成响应，
   再搜回去必然命中 → 报"泄漏"（自证循环）。
2. **逐 key 比对结构** —— `set(real.keys()) == set(fake.keys())`。
   一个字段对不上 → 前端渲染不出来 → 画面显示"暂无"。
3. **确认合成前缀不撞真实前缀** —— `SyntheticConfig(prefixes=...)` 构造时会抛异常。

详见 `references/adapter-guide.md`。
