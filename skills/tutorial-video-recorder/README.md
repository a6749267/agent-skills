# real-app-tutorial-video

把「真应用 + 合成数据」录成教程视频的完整管线。**换 app 只需改配置 + 一个 adapter 文件。**

---

## 快速开始

```bash
# 1. 依赖自检
bash scripts/install.sh

# 2. 配置
cp assets/templates/project.json ./project.json
cp assets/templates/script.json   ./script.json
#   → 改 project.json: base_url / 登录 / segments / synthetic 前缀
#   → 改 script.json:   旁白文案

# 3. 一键跑
bash scripts/run_all.sh project.json
```

产出：
```
work/out/final.mp4                横屏 1920x1080
work/out/final_sub.mp4      带字幕成片
```

---

## 目录

```
scripts/
├── install.sh        依赖自检/安装
├── run_all.sh        一键跑完整管线
├── tts.py            旁白生成(MiniMax)
├── synthetic.py      合成引擎(bundle字面量 + 业务字段 + 编号形状守卫)
├── interceptor.py    三层拦截(录制和验收共用这一份)
├── recorder.py       真录屏(Playwright + 黑场标记)
├── compose.py        横屏合成(黑场对齐 + 响度)
├── subtitle.py       字幕(切句 → PIL渲染 → overlay烧录)
├── verify.py         验收(全片OCR盲扫 + 原生OCR读数字)
├── seed_db.py        灌业务数据(可选)
└── adapters/
    ├── README.md
    └── futures_demo/adapter.py   ← 换 app 时复制这个改

references/
├── adapter-guide.md  接自己 app 的步骤
└── pitfalls.md       45 条踩坑手册(按症状索引)

assets/templates/
├── project.json      配置模板
└── script.json       旁白文案模板
```

---

## 换 app 改什么

| 要改的 | 在哪 |
|---|---|
| 应用地址 / 登录 / 模块路由 / 旁白 | `project.json` + `script.json` |
| 数据接口的响应结构 | `scripts/adapters/<你的app>/adapter.py` |
| 汇总字段重算 | 同上，`fix_summary()` |
| 真实前缀/名称表 | `assets/domains/<你的领域>.json` |

**通用层（recorder / compose / subtitle / verify / tts）不用改。**

---

## 字幕（必做）

只交付横屏 1920×1080，**必须配字幕**。

`subtitle.py` 自动做：`script.json` 的旁白 → 切句 → 按字数比例分配时间 →
PIL 渲染 PNG → ffmpeg `overlay` 烧录。

⚠ **不要用 ffmpeg 的 `subtitles` 滤镜** —— Homebrew 的 ffmpeg 默认不编译 libass，
报 `No option name near 'xxx.ass'`，三种转义写法都报同一个错。
用 `overlay`（内置滤镜一定有）。

⚠ **断句必须在标点处**，按字数硬折会切在词中间。
断句不自然是**文案问题** —— 改文案加个逗号，别用算法硬凑。

---

## 三条铁律

### 1. 验收对象 = 交付物本身
```bash
python scripts/verify.py --config project.json --video <你要发出去的那个mp4> --amplify
```
不要拿另一个脚本的通过结果给成片背书 —— 两者可能走不同 profile、不同拦截配置、
不同登录路径。

### 2. 数字用原生 OCR，不用模型读
`verify.py` 用 macOS 原生 Vision 引擎。**模型 OCR 有三种失败，看起来都像成功**：
回显候选词、思维链吃光 max_tokens、根本不认图。而且它会读错数字后
**自己算加法**，把错误结论当发现 —— 假不一致比真不一致更贵。

vision 只用来判断「模块在不在 / 是不是空态」，不让它读金额。

### 3. 数字要双验证
OCR 读出的每个金额，都要能用**源头算式**独立复算对上：
```
DB 明细 × 数据 last_price × 数量 = 明细差额
明细差额求和 = 顶栏汇总
```
只有模型一个人的证词 = 不可信。

---

## 依赖

```bash
pip install -r requirements.txt
playwright install chromium
brew install ffmpeg
```

TTS 需要 `MINIMAX_CN_API_KEY`（在 `$HERMES_HOME/.env` 或环境变量）。

---

## 已知会踩的坑

**45 条全在 `references/pitfalls.md`**，按症状索引。最高频的几条：

| 症状 | 看 |
|---|---|
| 画面像首页 / 像没登录 | pitfalls A2 |
| 顶栏和明细数字对不上 | pitfalls A4 |
| 任务卡片数值全是 0 | pitfalls A5 |
| 验证说干净但成片有泄漏 | pitfalls A1 |
| OCR 数字和实际对不上 | pitfalls A6 |
| 字幕烧录报错 | pitfalls D2 |
| 字幕断句怪 | pitfalls D2a |
| 人声听不见 | pitfalls D4 |
| 响度两片不一致 | pitfalls D4 |
