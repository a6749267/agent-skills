# Agent 技能集

一套**框架无关、可安装可移植**的 agent skill。每个 skill 一个目录、一份 `SKILL.md`、一个自检入口。

**当前收录**

| skill | 干什么 | 交付 |
|---|---|---|
| [`tutorial-video-recorder`](skills/tutorial-video-recorder/) | 录屏 → 合成 → 无声底版 | `silent.mp4` |
| [`tutorial-video-post`](skills/tutorial-video-post/) | 字幕 → 配乐 → 验收 → 脱敏 | `final.mp4` |

**框架无关**：所有 skill 都是标准 `SKILL.md` 目录结构，任何支持 skill 目录的 agent 框架都能直接用（Claude Code / Cursor / OpenClaw / Hermes / …），不绑定任何一家。运行时路径靠环境变量 + 优先级链解析，不硬编码任何框架的目录。

---

## 收录的第一个 skill：录屏教学视频管线

把一个**真实运行的 web 应用**录成教程视频，同时保证画面里没有一个真实数据。

**通用管线**：换 app 只需改一份配置 + 写一个 adapter，通用层一个字都不用动。

```
真实 web app
   │  ① Playwright 真录屏（完整 Chromium + 真前端 + 真路由）
   │  ② 三层拦截（L1 bundle 字面量 / L2 数据接口 / L3 业务接口）
   │  ③ 合成数据（你的领域词表在 assets/domains/）
   ▼
silent.mp4（画面+旁白，无字幕无音乐）
   │  ④ 字幕烧录  ⑤ BGM 下载/识别/选段/混音  ⑥ 成片验收  ⑦ 交付脱敏
   ▼
final.mp4（可直接发）
```

---

## 两个 skill 的分工

| skill | 干什么 | 交付 |
|---|---|---|
| [`tutorial-video-recorder`](skills/tutorial-video-recorder/) | 录屏 → 合成 → 无声底版 | `silent.mp4` |
| [`tutorial-video-post`](skills/tutorial-video-post/) | 字幕 → 配乐 → 验收 → 脱敏 | `final.mp4` |

配套方法论在 [`docs/`](docs/)：
- [系列规划](docs/series-planning.md) —— 排期、选题、脱敏铁律
- [发布门槛](docs/publishing-and-platform-gating.md) —— 标题/简介怎么写，受限领域怎么绕

---

## 这套东西解决什么问题

「录个教程视频」听起来简单，实际踩了 40+ 个坑，其中 4 个是致命的：

| 致命坑 | 后果 |
|---|---|
| 验收跑了另一个脚本，不是成片 | 用户看完直接判「你那张只是首页介绍页面，没登录进去」 |
| 新账号天然是空的 | 10 个模块 6 个显示「暂无」，像空壳 |
| 后端把汇总字段硬编码 0 | 同一画面顶栏 +0.00、明细 −1200.00，一眼看穿 |
| 数字靠模型读 | 模型把 −1200.00 读成 −1400.00，还自己算出「总数不一致」去质疑对的数据 |

完整清单见 [`skills/tutorial-video-recorder/references/pitfalls.md`](skills/tutorial-video-recorder/references/pitfalls.md)（分 A–H 八组，按症状索引）。

---

## 快速开始

### 1. 装

**通用方式**（任何 agent 框架）：把 `skills/` 下的目录复制到你框架的 skill 目录，或直接用脚本（不装 skill 也能跑）。

```bash
# 复制到你的框架的 skill 目录
cp -r skills/tutorial-video-recorder  <your-agent-skills-dir>/tutorial-video-recorder
cp -r skills/tutorial-video-post      <your-agent-skills-dir>/tutorial-video-post
```

如果你用的框架支持从 git 仓库拉取 skill 目录，直接指到本 repo 即可。

依赖：`ffmpeg` + `playwright`（`pip install playwright && playwright install chromium`）。
模型识别（AST/CLAP）另需 `torch transformers numpy`。

自检依赖是否齐全：

```bash
bash skills/tutorial-video-recorder/scripts/install.sh
```

`install.sh` 跑完依赖检查后会**自动跑一遍管线自检**（13 项）——
因为这套管线的错误**不会抛异常**，而是变成画面里「顶栏汇总 ≠ 明细之和」
这种看起来还挺像回事的假数据。肉眼发现不了，观众一眼看穿。

```bash
python skills/tutorial-video-recorder/scripts/selfcheck.py --verbose
```

### 2. 配

```bash
cp skills/tutorial-video-recorder/assets/templates/project.json ./project.json
cp skills/tutorial-video-recorder/assets/templates/script.json    ./script.json
```

三处要改：

| 改哪 | 怎么改 |
|---|---|
| `project.json` → `app` | 你的 app 地址、登录信息、每段路由与旁白 |
| `project.json` → `domain` | 你的领域配置名（见下） |
| `project.json` → `business_paths` | 你 app 的业务接口路径（支持通配 `/api/*/orders`） |

### 3. 写你的领域配置

```bash
cp skills/tutorial-video-recorder/assets/domains/generic.json my_domain.json
```

填三样东西：

```json
{
  "real_code_prefixes": ["sku", "ord"],          // 你的 app 里真实存在的短前缀
  "real_display_names": ["商品名甲", "客户乙"],     // 真实对象名
  "org_map": {"你的机构名": "合成机构A"},           // 机构名映射
  "id_shape": { "pattern": "^[0-9]{4}$", "prefix_len": 2, "year_digit": "2", "seq_min": 1, "seq_max": 12 }
}
```

> 🚨 **判定的权威永远是「你的 app 真实存在什么」**，不是任何通用词表。

### 4. 写 adapter

```bash
cp -r skills/tutorial-video-recorder/scripts/adapters/ecommerce_demo \
      scripts/adapters/my_app
```

只需实现两个方法（详见 [`references/adapter-guide.md`](skills/tutorial-video-recorder/references/adapter-guide.md)）：

```python
class Adapter:
    def route(self, url):
        """返回 (status, body) 替换这个响应; None = 不管"""
        if url.split("?")[0].endswith("/api/market/quotes"):
            return 200, self._shape_quotes(self.quotes())
        return None

    def fix_summary(self, url, payload, scfg):
        """修「顶栏汇总 ≠ 明细之和」; 不需要就原样返回"""
        return payload
```

**结构必须和真响应一模一样** —— 一个字段对不上，前端就渲染成「暂无」空态。

### 5. 跑

```bash
SKILL=skills/tutorial-video-recorder
python $SKILL/scripts/tts.py      --script script.json --out-dir work/audio
python $SKILL/scripts/recorder.py --config project.json
python $SKILL/scripts/compose.py  --config project.json
```

拿到 `silent.mp4` 后走后期：

```bash
POST=skills/tutorial-video-post
python $POST/scripts/subtitle_post.py --align align.json --script script.json \
    --timeline timeline.json --video silent.mp4 --out with_subs.mp4
python $POST/scripts/mix_bgm.py  --bgm music.mp3 --silent with_subs.mp4 \
    --timeline timeline.json --out final.mp4
python $POST/scripts/verify_audio.py final.mp4 --silent silent.mp4 --timeline timeline.json
python $POST/scripts/scan_leak.py final.mp4 --ocr --transcribe
```

---

## 设计要点

### 三层拦截（为什么一层都不够）

| 层 | 处理什么 | 只做这层的后果 |
|---|---|---|
| **L1** JS bundle | 前端代码里**硬编码**的真实字面量 | bundle 里的真编号照样上屏（DOM 洗不掉） |
| **L2** 数据类 API | 整个响应替换成合成 payload | 真实价格/数量/明细全泄漏 |
| **L3** 业务类 API | 放行真后端 + 字段级清洗 + 汇总重算 | 真账户上屏；顶栏和明细自相矛盾 |

### 通用层不含任何 app 假设

app 相关的路径、缓存键、领域词表**全在配置里**：

| 东西 | 在哪 |
|---|---|
| 业务接口路径 | `project.json` → `business_paths`（支持 fnmatch 通配） |
| 前端缓存键 | `project.json` → `storage_keys_block` |
| 领域词表 | `assets/domains/<name>.json` |
| 编号形状 | `assets/domains/<name>.json` → `id_shape` |
| 演示数据 | `project.json` → `db.demo` |
| 表名/字段名 | `project.json` → `db.schema` |

### 几个不显然但重要的点

- **必须用完整 Chromium**，不能用 `headless_shell` —— 后者不支持 `record_video` 的 screencast
- **每段一个独立 context** —— 段首状态干净，不会互相污染
- **浏览器磁盘缓存会让拦截完全失效** → 加 `--disable-http-cache`
- **拦截逻辑只能有一份**，录制和验收共用 → `interceptor.py::make_handler()`
- **数字必须用原生 OCR 读**，不用 vision 读 —— vision 在缩略拼图上会把 −1200.00 读成 −1400.00，然后自己算出「总数不一致」去质疑对的数据
- **全片 OCR 盲扫** —— 抽查几帧不能证明「没有泄漏」，泄漏可能在第 7 秒
- **hex 颜色守卫** —— `#ef4444` 是 CSS 颜色不是编号，不守会把前端配色全改坏

---

## 跨框架适配

两个 skill **不绑定任何 agent 框架**。所有与运行时环境相关的解析都集中在
`skills/tutorial-video-recorder/scripts/agentenv.py`，靠**优先级链**逐个尝试。

### 你可以设置的环境变量

| 变量 | 作用 | 默认回退 |
|---|---|---|
| `TUTORIAL_PYTHON` | 指定跑管线的解释器 | `$PY` → agent home 下的 venv → `python3` |
| `TUTORIAL_ENV_FILE` | 指定凭据文件 | `$AGENT_HOME/.env` → `~/.env` |
| `AGENT_HOME` | agent 运行时根目录 | — |
| `TUTORIAL_SKILL_HOME` | 指定 skill 根目录 | 脚本自身位置上溯两级 |
| `TUTORIAL_AGENT_HOME` | `AGENT_HOME` 的别名 | — |

### 为什么不用 `Path.home()`

> 🚨 agent 沙箱常把 `$HOME` 劫持成自己的沙箱家目录（形如 `<something>/home`），
> 真正的家目录和凭据文件在别处。`Path.home()` 会拿到沙箱路径 → 文件不存在 →
> **静默拿不到 key**，不报错，最难查。

`agentenv.real_home()` 的做法：从 PATH 上的 `python3` 真实路径**反推**家目录，
再退回 `expanduser("~")`。

### 自己装 skill 时的最小要求

只需要两样：

1. 目录里有 `SKILL.md`（YAML frontmatter，`name` 必须与目录名一致）
2. `scripts/` 与 `SKILL.md` 同级

---

## 依赖分层

不同脚本要不同的包，**别假设一个 venv 全都有**：

| 脚本 | 需要 |
|---|---|
| `fetch_music.py` / `mix_bgm.py` / `scan_leak.py` | 标准库（+ffmpeg）**零依赖** |
| `select_segments.py` / `verify_audio.py` | numpy |
| `subtitle_post.py` | pillow |
| `music_identify.py` / `ast_tags.py` / `clap_score.py` | torch + transformers |

模型识别（AST/CLAP）和 whisper 验收通常需要**两个不同解释器**。
逐个试：`python -c "import transformers"` / `python -c "import faster_whisper"`。

---

## 脱敏

**发布前必跑**：

```bash
python skills/tutorial-video-post/scripts/scan_leak.py publish_copy.md
python skills/tutorial-video-post/scripts/scan_leak.py final.mp4 --ocr --transcribe
```

退出码 0 才发。

你自己的私有词放 `assets/leak_patterns.json`（脚本**不硬编码任何人的私密身份**）：

```json
{
  "block": [["你的私有项目名", "私有项目"]],
  "warn":  [["某个易混淆的词", "自定义"]]
}
```

白名单放 `leak_allow.txt`（每行一个正则，命中即放行）。

---

## License

MIT
