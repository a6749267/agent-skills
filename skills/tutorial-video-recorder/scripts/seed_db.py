#!/usr/bin/env python3
"""灌业务数据到数据库 —— 让空账号「看起来像有人用过」。

用法:
    python seed_db.py --config project.json [--dry-run] [--schema-only]

🚨 六个必踩的坑(每一个都实测踩过):
  ① UUID 传 str() —— 传 UUID 对象会报类型错
  ② `offset` 是 SQL 保留字 → 要加引号 "offset"
  ③ 查 uuid 列用 user_id::text=%s —— 直接比 uuid 类型会类型不匹配
  ④ 同名对象跨盘唯一 → 表名/约束名要加后缀
  ⑤ 占位符数量对不上 → "not all arguments converted" → 用具名参数
  ⑥ 🚨🚨 `except: conn.rollback()` 会把整个事务回滚 ——
     一条失败、前面几百行全撤，脚本却打印「成功」→ 每步独立 commit，错误直接抛

🚨 本脚本**不含任何业务假设** —— 表名、字段名、枚举值、演示数据
   全部来自 project.json 的 `db` 段。换 app 只改配置。

用法要点:
  - 只写**自己那一个** user_id, 别碰别人的数据
  - 合成码用 QA~QH, 合成名「合成对象A..H」(或你配置的模板)
  - 灌完必须做三件事(见 pitfalls C3):
      ① 逐页 inner_text 抓裸露英文枚举
      ② 查编号前缀 ↔ 名称是否对位
      ③ 查该有数值的地方是不是 '--'
"""
import argparse
import json
import pathlib
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("❌ 需要 psycopg2 —— pip install psycopg2-binary")


def connect(dsn):
    return psycopg2.connect(dsn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--schema-only", action="store_true",
                    help="只列出库里的表和列, 用来填 config.json 的 db.schema")
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())
    db = cfg.get("db", {})
    if not db.get("enabled"):
        sys.exit("❌ config.json 里 db.enabled = false —— 不需要灌库")
    dsn = db["dsn"]
    user_id = cfg["app"]["user_id"]
    if not user_id and not a.schema_only:
        sys.exit("❌ config.json 里 app.user_id 为空 —— 必须是真实 user_id")

    conn = connect(dsn)
    conn.autocommit = True          # 坑⑥: 每步独立提交, 不让一条失败回滚全部
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print(f"连接成功  user_id={user_id}")

    if a.schema_only:
        print("\n=== 库里的表 ===")
        cur.execute("""select table_name from information_schema.tables
                       where table_schema='public' order by table_name""")
        for r in cur.fetchall():
            t = r["table_name"]
            cur.execute("""select column_name, data_type from information_schema.columns
                           where table_schema='public' and table_name=%s
                           order by ordinal_position""", (t,))
            cols = [f"{c['column_name']}:{c['data_type'][:12]}" for c in cur.fetchall()]
            print(f"  {t}\n      {', '.join(cols)}")
        print("\n把相关表名填进 config.json 的 db.schema:")
        print('  "schema": {')
        print('    "account_table":  "<账户表>",')
        print('    "record_table":   "<明细表>",')
        print('    "order_table":    "<订单表>",')
        print('    "list_table":     "<清单表>",')
        print('    "ledger_table":   "<流水表>"')
        print('  }')
        cur.close(); conn.close()
        return 0

    # ── 演示数据(合成) —— 全部来自配置 ────────────────────────
    demo = db.get("demo") or {}
    records = demo.get("records") or []
    orders = demo.get("orders") or []
    listing = demo.get("listing") or []
    ledgers = demo.get("ledgers") or []
    if not records and not orders and not listing and not ledgers:
        print("\n⚠️  config.json 里 db.demo 是空的 —— 没有数据可灌")
        print("   参考 db.schema 的字段, 在 db.demo 里写演示记录")
        return 2

    def run_sql(label, sql, params=None):
        if a.dry_run:
            print(f"  [dry] {label}: {sql[:70]}...")
            return
        try:
            cur.execute(sql, params or ())
            print(f"  ✅ {label}")
        except Exception as e:
            # 坑⑥: 不 rollback 整个事务, 直接抛
            print(f"  ❌ {label}: {e}")
            raise

    # ── 表名/字段名从配置读 ────────────────────────────────────
    # 🚨 不要把表名写死在脚本里 —— 换个 app 直接报表不存在, 但错误信息
    #    看起来像"数据库坏了", 很难定位。
    #    配置里给 schema; 没给就用 --schema-only 先探。
    schema = cfg.get("db", {}).get("schema") or {}
    if not schema:
        print("\n⚠️  config.json 里没有 db.schema —— 不知道表名")
        print("   先跑 --schema-only 看看库里有哪些表, 然后把表名填进 config.json")
        print("   (或者从你的 app 的 models.py / migrations 里抄)")
        return 2

    print("\n=== 灌业务数据 ===")
    try:
        T_ACC = schema["account_table"]
        T_REC = schema["record_table"]
        T_ORD = schema.get("order_table")
        T_LST = schema.get("list_table")
        T_LED = schema.get("ledger_table")
        F = schema.get("fields", {})

        # 账号: 按配置里的账号标识字段查
        mode_field = F.get("account_mode_field")
        mode_value = F.get("account_mode_value", "virtual")
        if mode_field:
            run_sql("查虚拟账户",
                    f"""select id from {T_ACC}
                        where user_id::text=%s and {mode_field}=%s""",
                    (str(user_id), mode_value))
        else:
            run_sql("查账户",
                    f"""select id from {T_ACC} where user_id::text=%s""",
                    (str(user_id),))

        row = cur.fetchone()
        if not row:
            print(f"  ⚠️  该 user 没有 {mode_value} 账户 —— 先在 app 里开一个")
            return 1
        account_id = row["id"]

        # ── 明细 ──
        if records:
            cols = [c for c in (F.get("record_cols") or
                    ["code", "direction", "quantity", "unit_price"]) if c]
            ph = ",".join(["%s"] * (len(cols) + 5))
            for rec in records:
                vals = [account_id, str(user_id)] + [rec.get(c) for c in cols] + [0, "now()", "now()"]
                run_sql(f"明细 {rec.get('code')}",
                        f"""insert into {T_REC}
                           (account_id, user_id, {", ".join(cols)},
                            "offset", created_at, updated_at)
                           values ({ph})""",
                        tuple(vals))

        # ── 订单 ──
        if T_ORD and orders:
            cols = [c for c in (F.get("order_cols") or
                    ["code", "side", "quantity", "price"]) if c]
            ph = ",".join(["%s"] * (len(cols) + 3))
            for o in orders:
                vals = [account_id, str(user_id)] + [o.get(c) for c in cols] + \
                       [o.get("status", "filled"), "now()", "now()"]
                run_sql(f"订单 {o.get('code')}/{o.get('side')}",
                        f"""insert into {T_ORD}
                           (account_id, user_id, {", ".join(cols)},
                            status, created_at, updated_at)
                           values ({ph})""",
                        tuple(vals))

        # ── 清单 ──
        if T_LST and listing:
            cols = [c for c in (F.get("list_cols") or ["code", "name"]) if c]
            ph = ",".join(["%s"] * (len(cols) + 2))
            for it in listing:
                vals = [str(user_id)] + [it.get(c) for c in cols] + [0, "now()"]
                run_sql(f"清单 {it.get('code')}",
                        f"""insert into {T_LST}
                           (user_id, {", ".join(cols)}, sort_order, created_at)
                           values ({ph})""",
                        tuple(vals))

        # ── 流水 ──
        if T_LED and ledgers:
            cols = [c for c in (F.get("ledger_cols") or
                    ["flow_type", "amount", "memo"]) if c]
            ph = ",".join(["%s"] * (len(cols) + 2))
            for lg in ledgers:
                vals = [account_id, str(user_id)] + [lg.get(c) for c in cols] + ["now()"]
                run_sql(f"流水 {lg.get('flow_type')}",
                        f"""insert into {T_LED}
                           (account_id, user_id, {", ".join(cols)}, created_at)
                           values ({ph})""",
                        tuple(vals))

        print("\n✅ 灌库完成")
        print("\n⚠️  接下来必做三件事(pitfalls C3):")
        print("   ① 逐页 inner_text 抓裸露英文枚举")
        print("   ② 查编号前缀 ↔ 名称是否对位")
        print("   ③ 查该有数值的地方是不是 '--'")
        return 0
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
