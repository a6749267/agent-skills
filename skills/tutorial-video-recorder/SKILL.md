---
name: tutorial-video-recorder
description: >
  把「真应用 + 合成数据」录成教程视频并交付 —— 通用管线，adapter 接任意 web app。
  触发语：「录个教程视频」「把应用录成视频」「做一期教学视频」「录屏成片」
  「真录屏」「视频脱敏」「把界面录下来讲」「做教程视频」「加字幕」
  「录出来像首页」「怎么让录屏里界面不是空的」「加片头片尾」「加配乐」「字幕对不上」
  「这个视频是总体介绍」「接下来要做的视频有规划吗」「再拍几期」「选题怎么排」「视频系列规划」
  「把标题和简介写出来」「发布时被卡审核」「流量受影响怎么办」「能发吗」
  核心资产：scripts/ 里的 recorder / synthetic / interceptor / compose / subtitle / asr_align / visual / endcard / verify / tts / pace / mix_dynamic_music，
  全部可参数化运行。换 app 只需改 assets/templates/project.json + assets/domains/<你的领域>.json + scripts/adapters/<你的app>/adapter.py。
  配套踩坑手册（references/pitfalls.md，按症状分 A-H 八组）、adapter 接入指南（references/adapter-guide.md）、
  节奏与画面规范（references/pacing-and-visual-spec.md）、字幕强制对齐配方（references/subtitle-pipeline.md）。
---

# 录屏教学视频管线（录制端）

把一个**真实运行的 web 应用**录成教程视频，同时保证：
- 画面是**真界面真交互**（不是拼图、不是官网营销页）
- 画面里**没有一个真实数据**（对象/编号/账户/业绩全部合成）
- 画面里**数字自洽**（顶栏汇总 = 明细之和 = 卡片数值）
- 只交付**横屏 1920×1080**（竖屏不做），**必须配字幕**，且**验收针对成片本身**
- 画面**不能是死画面** —— 录屏静止 20 秒不动一像素 → 观众划走（用 `pace.py` 帧差体检）

---

## 这条管线解决的是什么问题

「录个教程视频」听起来简单，实际踩了 40+ 个坑，其中 4 个是致命的：

| 致命坑 | 后果 |
|---|---|
| 验收跑了另一个脚本，不是成片 | 用户看完直接判「你那张只是首页介绍页面，没登录进去」 |
| 新账号天然是空的 | 10 个模块 6 个显示「暂无」，像空壳 |
| 后端把汇总字段硬编码 0 | 同一画面顶栏 +0.00、明细 −1200.00，一眼看穿 |
| 数字靠模型读 | 模型把 −1200.00 读成 −1400.00，还自己算出「总数不一致」去质疑对的数据 |

这份 skill 就是把这些坑固化成代码和检查项。**完整清单见 `references/pitfalls.md`（分 A–H 八组，按症状索引）**。

---

## 五步走

```bash
SKILL=<本 skill 目录>

# 0. 依赖自检
bash $SKILL/scripts/install.sh

# 1. 改配置
cp $SKILL/assets/templates/project.json ./project.json
#    → 填 base_url / 登录信息 / segments(模块+路由+旁白) / synthetic 前缀
#    → 也填 endcards(片头片尾文案) / music(配乐路径) / align_path

# 2. 生成旁白
python $SKILL/scripts/tts.py --script script.json --out-dir work/audio

# 3. 录屏
python $SKILL/scripts/recorder.py --config project.json

# 3.5 🚨 生成 ASR 对齐轴(字幕时间轴的唯一正确来源)
python $SKILL/scripts/asr_align.py \
    --script script.json --audio-dir work/audio --out work/align.json
#    → 必须做! 不做的话字幕按字数摊时间, 每段偏 0.3~1.5s(实测), 字幕跟不上人声
#    → 首次运行会下载 faster-whisper small 模型(~460MB)
#    → 网络不通时: export HF_ENDPOINT=https://hf-mirror.com

# 4. 合成(横屏 + 字幕 + 片头片尾 + 配乐 —— 一次编码)
python $SKILL/scripts/compose.py --config project.json
#    → work/out/final.mp4  ← **已经带字幕**(字幕在拼片阶段就烧进去了)
#      没有 final_sub.mp4 —— 那是旧的两遍编码做法, 会把顶栏小字压糊(见 pitfalls D2c)

# 5. 验收(针对成片本身 —— 就是上面那个 final.mp4)
python $SKILL/scripts/verify.py --config project.json --video work/out/final.mp4 --amplify
#    看阶段【4. 字幕覆盖率】—— 确认字幕真的进成片了

# 6. 节奏体检(视觉层有没有起作用)
python $SKILL/scripts/pace.py work/out/final.mp4
#    目标: 变化强度 ≥3.0 | 变化帧 ≥30/分 | 最长静止 ≤6s | 静止占比 ≤25%
```

**接自己的 app** → 看 `references/adapter-guide.md`。
**撞坑了** → 看 `references/pitfalls.md`（按症状分 A–H 八组，每条都是「症状 → 根因 → 解法」）。
**画面闷 / 节奏差** → 看 `references/pacing-and-visual-spec.md`。
**字幕跟不上人声** → 看 `references/subtitle-pipeline.md` §1（强制对齐配方）。

### 🚨 交付物 = 成片文件本身，不是「我做好了」

用户原话：「**做好的视频呢**」

→ 视频这类任务，**交付物是那个 mp4 文件**。
把成片路径直接放进回复里（`MEDIA:<绝对路径>`），
或立刻 `send_message` 带 `MEDIA:` 发出去 —— **别让用户再来问一次「视频呢」**。

同理：报告可以写，但**成片必须在同一条消息里给到**。
「已完成 + 路径在某某目录」这种交付在对话里等于没交付。

---

## 🚨 用户说「参考这个视频」时，先分清他要的是什么

用户原话：「**他们的内容不是重点，我是让你看他们两个视频，参考节奏和画面来优化我们的视频**」

→ 同一个动作有**两个完全不同的产出**：
- **内容/技术侦察**（对方讲了什么、用了什么技术、仓库在哪）→ 产出是知识/代码
- **节奏与画面**（对方怎么剪、怎么排版、镜头多密、字幕什么样式）→ 产出是**我们视频的改造项**

**信号词**：说「内容不是重点」「参考节奏和画面」「优化我们的视频」→ 就是后者。
这时**不要**花大量篇幅去分析对方的技术栈/项目内容（那是另一条任务线），
而是**量化对方的画面语言**并落到我们的 skill 里。

**量化方法**（别凭感觉说「对方节奏快」）：
```bash
python scripts/pace.py 对标视频.mp4 our.mp4 --compare
```
→ 变化强度 / 变化帧每分钟 / 最长静止段 / 静止占比，四个硬指标直接对比。

---

## 架构：通用层 + adapter

```
通用层（不用改）                    adapter（每个 app 改这个）
├── recorder.py    录制              └── adapters/<你的app>/adapter.py
├── interceptor.py 三层拦截                   ├── route()       数据接口整体替换
├── synthetic.py   清洗+合成池                 └── fix_summary() 汇总字段重算
├── compose.py     横屏合成+黑场对齐
├── subtitle.py    字幕(切句→PIL渲染→overlay烧录)
├── asr_align.py   ASR强制对齐(逐字时间戳 + 与脚本原文对齐)
├── visual.py      视觉层(标题压屏/框选标注/推镜/聚光)
├── endcard.py     片头/片尾(暗底网格+动效+要点收尾)
├── pace.py        节奏体检(变化强度/静止段/硬切点)
├── verify.py      验收(OCR盲扫+数字+字幕覆盖率)
└── tts.py         旁白
```

**拦截三层（从外到内）**：

| 层 | 拦什么 | 做什么 | 为什么不能少 |
|---|---|---|---|
| L1 | JS bundle | 改前端**硬编码**的字面量 | 只做 L2 → bundle 里的真编号照样在画面上（DOM 洗不掉） |
| L2 | 数据类 API | 整个响应替换成合成 payload | 只做 L3 → 泄漏面是整个数据面（代码+价格+盘口+数量） |
| L3 | 业务类 API | 放行真实后端 + 字段级兜底 + 汇总重算 | 只做 L1 → 业务接口返回真库里的东西 |

**一条铁律**：L3 的实现**只能有一份**，录制和验收共用 `interceptor.make_handler()`。
历史上 verify 脚本 import 了清洗模块却没在路由里用 → 报「零泄漏」是侥幸。

---

## 字幕（必做）

**只交付横屏 1920×1080，且必须配字幕。**（竖屏已永久废弃 —— 用户定的规矩。）

字幕由 `subtitle.py` 自动生成：`script.json` 的旁白文案 → 切句 → PIL 渲染成 PNG →
ffmpeg `overlay` 烧录。

### 🚨 字幕时间必须用 ASR 强制对齐，不能按字数摊

**用户原话：「字幕还是不要了，你做的不好还不如自动识别生成」** ——
问题不在"要不要字幕"，在**字幕跟不跟得上人声**。按字数摊的时间是**猜的**：
TTS 语速不均匀（停顿/语速/句长都不同），每段偏 0.3~1.5s，累积到片尾能偏好几秒，
观众看到的就是「字幕比人声慢半拍」。

**正解 = 强制对齐（forced alignment）**：
```bash
# ① ASR 出逐词时间戳, 并与脚本原文对齐(时间取ASR, 文字取原文)
python scripts/asr_align.py --script script.json --audio-dir audio --out align.json
# ② 配置里指向对齐轴, compose 自动优先使用
{ "align_path": "align.json" }
```
- **时间取自 ASR**（真实语音），**文字取自脚本原文**（零错字）。
  ⚠ ASR 中文同音字错很多：终端→中端、这一屏→这一瓶、合成→审查、全废→权费。
  所以**绝不能**直接用 ASR 的识别文本当字幕。见 pitfalls G16。
- ⚠ 对齐轴里**没有标点** → 切句必须用脚本原文的标点，再映射回对齐轴。
  见 pitfalls G17。
- 兜底：找不到 `align.json` 时退回按字数摊，但会**打印警告**（不静默）。

**为什么不用 ffmpeg 的 `subtitles` 滤镜**：Homebrew 的 ffmpeg 默认**不编译 libass**
（`ffmpeg -filters | grep subtitles` 为空）→ 报 `No option name near 'xxx.ass'`。
三种路径转义写法都报同一个错，会一直调错方向。
`overlay` 是内置滤镜一定有。**见 pitfalls D2。**

**字幕必须在拼片阶段烧**（不能成片后再烧）：二次编码会把顶栏小字压糊，
连累验收读不出「−1,652.90」。**见 pitfalls D2c。**

**断句必须在标点处**：按字数硬折会切在词中间，出现「…是一个模块」/「一个模块搭起来的」
这种视觉重复。**断句不自然是文案问题 —— 改文案加个逗号，别用算法硬凑。** 见 pitfalls D2a。

**标点永远优先于长度**：两行排不下时，宁可第一行长一点（≤1.5×）也要切在标点上。
死守「每行 ≤max_chars」会退回硬折 → 拆词（「…用的哪段行」/「情、」）。
切句阈值取 `max_chars*1.5`（30 字）—— 40 字太多条折行拆词，20 字太多条字幕闪太快。

**验收要扫带字幕那版** —— 字幕也是画面内容，OCR 盲扫必须包含它。

### 🚨🚨 交付前必查：字幕到底进没进成片

**这条是血泪换来的。** 字幕被**静默跳过**过一次 —— `script_path` 相对 CWD 解析失败，
代码写成「找不到就跳过、不报错」，流程 0 退出成功，顶栏像素还和无字幕版完全一致，
**所有自动化验收全过** —— 但交付物**根本没有字幕**，是用户发现的。

**三道必查**：
```bash
# ① 视频流必须只有 1 条(有字幕时 map 了原片+字幕片 = 两条 = 播放器放第一条 = 白烧)
ffprobe -v error -select_streams v -show_entries stream=index -of csv=p=0 成片.mp4
# ② 字幕**内容**对不对轴 —— 抽帧裁字幕区, 交给视觉模型 OCR, 与对齐轴期望值比对
#    (macOS Vision 读不了这种裁剪图, 必须用更强的视觉模型)
# ③ verify.py 阶段【4. 字幕覆盖率】—— 像素级检测, <90% 判失败
python scripts/verify.py --config project.json --video 成片.mp4 --amplify
```
⚠ 覆盖率检测必须排除**四种**无字幕区间：`title_card` / `intro` / `outro` /
**段尾静音尾巴**（段落视频比旁白长，旁白说完后画面还在但没字幕 —— 设计如此）。
只排除 title_card 会误判成 87.6% → 误报"成片没有字幕"。见 pitfalls G20。

**铁律：任何"可选特性"的开关，要么真的开，要么报错退出，绝不能静默降级。**
见 pitfalls D2e / D2f / D2g / F5 / G16 / G20。

**数字判读用现成脚本**（两种读法取并集 + 与源头算式交叉验证）：
```bash
python scripts/read_numbers.py --video out/final.mp4 --times 12,22,32 \
    --crop 250,0,800,60 --crop 250,120,900,400 \
    --expect -1400.00 --expect 1020000.00
```
⚠ **两种读法都要做**（整帧放大 + 区域裁剪）—— Vision 的行聚类对输入布局敏感，
整帧能读出的数裁成细条可能读不出，反之亦然。**数字取并集。**

完整配方（时间轴算法 / PIL 渲染参数 / overlay 滤镜链 / 验收两种读法）→
**`references/subtitle-pipeline.md`**。

---

## 视觉层（必做 —— 否则画面是死的）

**录屏天生是死画面**：界面静止 20 秒不动一个像素 → 观众 3 秒划走。
实测改造前成片：变化强度 0.88、最长静止段 15s、静止占比 76.3%。

`scripts/visual.py` 提供四个手法（三个参考视频验证过的共通解法）：

| 手法 | 作用 | 关键参数 |
|---|---|---|
| **大标题压屏** | 每段前插 1.9s 静音章节卡：模糊底 + 超大粗衬线标题 + 荧光色关键词 | `visual.title_card` |
| **框选标注** | 青绿圆角框圈住关键数字，观众知道看哪 | `visual.highlight` + script.json 的 `focus` |
| **推镜** | 整帧缓慢漂移，每帧都在动 → 死画面归零 | `visual.push` |
| **聚光** | 焦点区留亮、其余压暗 | `visual.render_spotlight` |

**script.json 每段可配**：
```json
{
  "title": "防自己骗自己",
  "keyword": "骗自己",
  "card_sub": "先把参数锁死，再看结果",
  "module": "历史记录",
  "focus": [{"match": "分析报告", "label": "分析报告（故意留空）", "at": 5.0, "dur": 7.0}]
}
```
- `keyword` **必须是 title 的子串**（否则静默不染色）
- `focus.match` 是**屏幕上真实存在的文字** → 用 Vision OCR 自动定位
- `focus.at` 是**段内相对秒数**（不含标题卡）

### 实测效果（本 skill 自己的成片，改造前 → 改造后）

| 指标 | 改造前 | 加视觉层 | +片头片尾+配乐 | 目标 |
|---|---|---|---|---|
| 变化强度（帧差均值） | 0.88 | 4.76 | **5.22** | ≥ 3.0 |
| 变化帧/分钟 | 14.2 | 58.8 | **58.9** | ≥ 30 |
| 最长静止段 | 15s | 1s | **1s** | ≤ 6s |
| 静止画面占比 | 76.3% | 2.0% | **1.9%** | ≤ 25% |

**交付前必跑节奏体检**：
```bash
python scripts/pace.py work/out/final.mp4
# 目标: 变化强度 ≥3.0 | 变化帧 ≥30/分 | 最长静止 ≤6s | 静止占比 ≤25%
```

## 片头 / 片尾（必做 —— 用户明说「没有做片头和片尾」）

片子一上来就是录屏界面，观众 3 秒内不知道这是什么；结尾直接黑屏，又显得仓促。
`endcard.py` 负责这两段，compose 自动调用。

```json
"endcards": {
  "intro": {
    "enabled": true,
    "title": "手搓研究终端",
    "sub": "从数据管道到界面",
    "badge": "全流程拆解",
    "duration": 4.2
  },
  "outro": {
    "enabled": true,
    "points": ["数据管道:来源可追溯", "实验室:参数先锁死再跑", "分析:每个数字能追到源头"],
    "next": "数据层与界面再拆开讲",
    "duration": 4.6
  }
}
```

**设计要点（与标题卡同一套视觉语言，别风格割裂）**：
- 片头：暗底 + 流动网格 + 辉光点 + 顶部栏目胶囊 + 超大粗衬线标题 + 荧光下划线 + 副标题 + 底部等宽字条
- 片尾：同款暗底 + 收尾标题 + 编号要点 + 「下期预告」标签胶囊 + 免责声明

**🚨 片头片尾不能是死画面** —— 它们是全片仅有的纯图形段落，更要有动感
（网格流动 / 推镜 / 拉远 / 淡入淡出）。每帧都在动是这一层的存在意义。

**🚨 评审实测要修的点**（视觉模型 review 会直接指出来）：
- 装饰圆**太大太亮**会抢主标题 → 缩小 + 压暗 + 外移
- 背景网格**在正文区太吵** → 文字区铺一层暗色蒙版（`_quiet_zone`）
- 网格**横竖交点**在底部会被看成"一排小圆点" → footer 区也要压暗
- 正文描边**过重**会显糊 → 白字在深底上本就有对比，描边 1~2px 足够
- 底部文字**安全边距不足**会被平台裁切 → 上移 + 留足边距

**帧数必须取整**（和标题卡同规矩），否则 concat 留半帧空洞 → 容器推断 50fps。

## 配乐（必做 —— 用户明说「没有激情的配乐」）

```json
"music": {
  "path": "/path/to/bgm.mp3",
  "gain_db": -13.0,
  "start_offset": 8.0
}
```

**🚨 配乐必须闪避旁白（ducking）**，否则音乐盖过人声 → 观众听不清解说。
用 `sidechaincompress`，旁白当触发源。见 pitfalls G18。

#### 🚨🚨 分段动态配乐（用户明确要求："开头/正文/结尾音量和内容应该不同，口播声音应该大些"）

**不是把整条音轨调个音量** —— 是**三段各自用曲子的不同段落 + 不同音量 + 口播主导**。

```bash
# ① 先分析曲子结构(1s 粒度能量/低频/onset) → 挑三段素材
python scripts/music_curve.py bgm.mp3
#    片头 ← 能量最高潮处(onset峰值)  正文 ← 连续驱动段  片尾 ← 强收束句
# ② 分段动态混音(时间轴从 timeline.json 读, 三段各自 atrim+volume+afade+adelay 对齐)
python scripts/mix_dynamic_music.py bgm.mp3 out.mp4 silent.mp4
# ③ 逐段实测验证(必须! 不能凭感觉)
python scripts/verify_music_dynamic.py out_musiconly.wav out.mp4
```

**四个必须避开的顺序错误**（每个都会"改了参数但实测没变化"）：
- **终混末端不能过 `loudnorm`** → 它是整片一次性归一化，会把三段相对关系压平。
  loudnorm 只做在口播轨上，终混末端只留 `alimiter`。
- **闪避只做正文段** → 片头/片尾无旁白，`makeup` 会把音乐往上抬，导致正文最轻（与设计相反）。
  🚨🚨 **但这条有前提**：只在「片头/片尾真的没有旁白」时成立。
  如果片头是一段**有解说的开场**（v9 就是这样：intro 段 0~24s 全是旁白），
  照搬"片头直通"会**让音乐直接盖死开场白** —— 实测 AST Speech 只有 0.18（几乎听不清）。
  → **先确认每段有没有旁白**，再决定闪避范围。有旁白的段一律要闪避。
  → 闪避参数**分段设**（body 强 / intro·outro 弱），否则音乐被压成同一层、段落氛围全丢。
  详见 pitfalls **G25**。
- **按时间轴对齐** → intro 音乐要覆盖「片头+首张标题卡」，否则标题卡区一片死寂。
- **区间增益用独立 `volume` + `enable`**，别用表达式（表达式的 `t` 是流内时间轴）。

**验证要分开量三个指标**：口播轨 LUFS / 音乐贡献 LUFS（口播权重置 0 导出）/ 成片 LUFS。
「成片响度达标」≠「口播清楚」。再拿 AST 模型听一遍混音后的音频验语音清晰度。
完整踩坑见 pitfalls **G23 / G24**。

**🚨🚨 串联两个 `atrim` 会让配乐末尾凭空消失**（只输出 202.13s，应为 210.13s）→
片尾没配乐。必须用**单个 atrim**：`atrim=start=OFFSET:end=OFFSET+总长`，
循环交给 `-stream_loop -1`。见 pitfalls G19。

**🚨 曲目开头常是静音淡入**（实测 594 号 0~6s 是 -90dB）→ 片头会没声音，
必须配 `start_offset` 从响亮段开始。

**混完必须再做 loudnorm** —— 只用 alimiter 实测只有 -35.3 LUFS（目标 -16）。

### 🚨 选曲方法（别凭感觉挑，也别只看频谱图）

> **踩坑血泪**：曾经只用「频谱三项打分」选曲，指标全中，用户听完直接骂
> "你配的那个是什么垃圾配乐！"。**频谱图回答不了"激不激情"** ——
> 高端 Corporate 配乐和真带劲电子乐频谱几乎一样。详见 pitfalls **G21**。

**四步，缺一不可：**

#### ① 候选池先按 genre 筛（最省事、最致命的一步）
**池子错了，后面全是白干。** 只抓 `epic/cinematic/technology/upbeat/trailer/dramatic`
→ 全是「电影感/企业科技」，**一条 techno/drum-and-bass/house/edm 都没有** →
选出来的必然是 corporate 励志垃圾（实测被选中的 594 号 genre = `cinematic+dramantic`）。

```bash
# mixkit genre 列表页 → 曲子 id
python scripts/fetch_hot_genres.py    # 抓 techno/house/trance/edm/dubstep/funk/rock/hip-hop...
# ⚠ 每个 genre 间隔 ≥1s，429 要退避（见 pitfalls G22）
# 下载: https://assets.mixkit.co/music/<id>/<id>.mp3
```
**动感池**：`techno / drum-and-bass / house / electronic / trance / edm / dubstep /
breakbeat / funk / rock / hip-hop / bass / groove / synthwave / action / sports`
**排除**：`cinematic / ambient / chillout / corporate`（除非明确要电影感）

#### ② 用真模型"听"（不是看图）—— 本机可跑
```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install transformers numpy
export HF_ENDPOINT=https://hf-mirror.com   # 直连 huggingface.co 在大陆超时
```
- **AST** `MIT/ast-finetuned-audioset-10-10-0.4593` → 527 类 AudioSet 标签。
  出 `Trance music / Techno / Electronic music / House music / Drum machine / Bass drum`
  = 带劲；出 `Violin / Cello / Sad music / Ambient music` = **电影/悲伤配乐，直接排除**。
- **CLAP** `laion/clap-htsat-unfused` → **零样本音频-文本匹配**。
  正向提示（`energetic electronic with driving beat and build up`）
  减负向提示（`boring generic corporate stock loop`）= MARGIN。
  **MARGIN < 0 = 比 corporate 垃圾还像垃圾。**
  ⚠ transformers 5.x：`get_text_features` 可能返回 ModelOutput 要取 `pooler_output`；
  `proc(audios=)` 已改名 `proc(audio=)`。

#### ③ 能量结构验证（墙纸 vs 驱动）
每 10s 能量包络 → 前段能量 / 爬升斜率 / 起伏次数 / 低频驱动强度。
**起伏次数太少 = 墙纸音乐**（实测 594 号 90s 只有 13 次起伏，全程平坦）；
带劲的电子乐 30~90 次。

#### ④ 时长与闪避适配
- 曲目时长 **≥ 成片时长**（免循环接缝）；不够就挑结构好的循环点。
- 曲子**开头常是静音淡入**（594 号 0~6s 是 -90dB）→ 必须配 `start_offset`。
- **闪避必须验**：逐秒对比「成片 vs 纯旁白」平均电平 ——
  片头/片尾（无旁白）应明显更响；旁白段应只高 10~20dB（音乐垫底不抢人声）。

**免费可商用曲库**：mixkit（`https://assets.mixkit.co/music/<id>/<id>.mp3`，
Mixkit License，可商用无需署名）。**注意**：GitHub/HuggingFace 在部分网络不通，
`hf-mirror.com` 可用；MusicGen 本地生成在 CPU 上不现实。

**🚨 交付前必须真的"听"过**：用 AST/CLAP 出结论，或做 25s 试听片段让用户亲耳选。
**不要拿频谱指标当"我验证过了"。**

### 🚨 视觉层必踩的坑

1. **`-t` 放 `-i` 前是输入选项** → 只限音频那一路，视频超长 1.3s
   → 字幕掉到 74% **且所有时间轴错位**（标题卡/高亮框/字幕全偏）。
   `-t` 必须放**命令末尾**（输出选项）+ `[1:a]apad` 补静音。**见 pitfalls G1。**
2. **推镜会裁掉画面边缘** → 顶栏标签牌放框上方会被裁没。
   安全边距 = `H*(Z-1)/Z`（不是 `H*(Z-1)/2`）。**见 pitfalls G6。**
3. **标题卡故意没有字幕** → 验收算覆盖率必须排除这些区间，
   否则**误报「成片没字幕」**（compose 已输出 `timeline.json`）。**见 pitfalls G9。**
4. **帧数必须取整** → 容器时长必须 = 帧数/fps。否则 concat 按容器时长算偏移，
   每段后面留半帧空洞 → 容器推断成 50fps（实际 25）→ 部分播放器/剪辑软件错帧。
   视频用 `-frames:v N`，音频用 `-t N/fps`，两者严格相等。**见 pitfalls G14。**
5. **高亮框定位不到必须报错退出**，不能静默跳过 —— 交付物看着正常但其实没框
   （和字幕静默跳过是同一类病）。**见 pitfalls G10。**
6. **OCR 定位小字必须放大 2×** → 原生分辨率读出来是乱码。**见 pitfalls G8。**
7. **`-map` 顺序 = 流顺序**，视频必须在音频前面，否则 concat 失败。**见 pitfalls G2。**
8. **`-map` 引用的输入下标必须严格等于 `-i` 的添加顺序** → 算错会让高亮框指向
   字幕 PNG、字幕指向高亮框 → 框没画出来 + 大段字幕丢失。**见 pitfalls G5。**
9. **字幕时间按字数摊 = 猜的** → 用 ASR 强制对齐。**见 pitfalls G16/G17。**
10. **配乐必须闪避旁白**，且**不能串联两个 atrim**（末尾会消失）。**见 pitfalls G18/G19。**
11. **验收排除的无字幕区间有四种**：title_card / intro / outro / 段尾静音尾巴。
    少排除一种就误报"成片没字幕"。**见 pitfalls G20。**

完整配方（标题卡排版参数 / 荧光色章节配色 / 安全区算法 / 客观验收方法 /
对标视频取样手法）→ **`references/pacing-and-visual-spec.md`**。

片头片尾的排版参数 + 评审要修的点 → **`references/pacing-and-visual-spec.md`** 的片头片尾章节。

---

## 三个必须记住的判断

### 1. 验收对象 = 交付物本身
```bash
python scripts/verify.py --config project.json --video <你要发出去的那个mp4> --amplify
```
不要拿 `verify_synth.py` 之类的通过结果给成片背书 —— 两者可能走不同 profile、
不同拦截配置、不同登录路径。DOM 判定只能用来**定位哪一段有问题**。

### 2. 数字用原生 OCR，不用模型读
```bash
# verify.py 已经这么做了 —— 你要做的只是 --amplify
python scripts/verify.py --config project.json --video final.mp4 --amplify
```
macOS 原生 Vision 引擎，本地、免费、无限速、中文强。
**模型 OCR 有三种失败，看起来都像成功**：回显候选词、思维链吃光 max_tokens、
根本不认图。而且它会读错数字后**自己算加法**并把错误结论当发现。

**vision 只用来判断「模块在不在 / 是不是空态」，不让它读金额。**

### 3. 数字要双验证
OCR 读出的每个金额，都要能用**源头算式**独立复算对上：
```
DB 明细 × 数据 last_price × 数量 = 明细差额
明细差额求和 = 顶栏汇总
```
算式对得上 + OCR 读数对得上 = 真一致。
**只有 vision 一个人的证词 = 不可信。**

---

## 系列化：第一支片之后怎么排后面的期

**总介绍片（v6 这类）是 Trailer，不是 EP1。**
它已经把每个模块各露了一面 + 结尾埋了伏笔 → 之后每期的单位是
**「一个真实问题 + 一屏实操 + 一个能自己验的数字」**，
🚨 **不要再做「模块介绍」**（重复 = 浪费一期）。

排期前必做一次**本机真实资产盘点** —— 但**先确认这个 app 由谁维护**，
再去决定去哪找素材（⚠️ 素材可能不在你自己的项目里 —— 先 grep 确认实现真在哪，见 repo 的 docs/series-planning.md §3）。  # leak-ok
**只保留背后有可验证文件/接口的选题**，凭空列的选题拍的时候没素材，整期作废。

💎 **优先挖「代码里自己承认的边界」**（源码注释里的「这块还没做」）
= 天然反常识 + 天然建立信任 + 天然合规，**一期至少要有一处**。

🚨 **分发层会被用户拍板推翻**（例如定：**不做短视频平台**）—— 规划前先问「做哪些平台」，
别把调研报告里的分层架构当既定事实。

🚨 **脱敏要点逐期单列**：某期的魅力可能全在真实实测数字上（EP2 的 3.01% vs 1.56%），
通用规则不够用 → 换**合成的同结构数字**（结构真、数值假），观众能跟着推但不泄底。

完整方法（资产盘点顺序 / 跨 profile 找素材 / 标题模板 / 分发三层 / 单期成本 /
全系列统一规范 / 每期脱敏 / 待拍板项写法 / 定稿动作 / 反面模式 +
2026-11-23 完整选题表工作样例）
→ **repo 的 `docs/series-planning.md`**。

---

## 🚨 发布：成片做完 ≠ 发布完

成片交付之后还有**两件独立的事**：**文案**（决定有没有人点）+ **平台门槛**（决定能不能被推荐）。
🚨 **只写文案不查门槛 = 交付一个拿不到流量的成片。** 2026-11-23 实撞：
文案写完、脱敏通过、发布 → B站返回「**流量受影响**」。

**三件套 + 三个硬约束**
| 件 | 硬约束 |
|---|---|
| 标题 | 反常识钩子 + 具体对象；给 3~4 个候选 + 主推 + 理由；**无收益暗示/荐股语义/外链** |
| 简介 | 章节时间戳 + 内容说明 + 免责声明 + 系列预告；**不出现联系方式/二维码/微信/谐音** |
| 标签/分区 | 标签吃搜索词；**分区决定审核口径** |

**🚨 章节时间戳必须从 `timeline.json` 推导，不能估** —— 观众会按它跳，估错比不写更糟。
每段带 `card_sub`，信息量比只写段名大得多。推导脚本见
`docs/publishing-and-platform-gating.md` §2。

### 🚨🚨 B站「财经金融专业领域内容投稿」= 视频形态在财经领域被卡

**先分清卡的程度**：徽章是「**流量受影响**」→ 稿件还在、能播，但推荐与搜索排序被压低
（可救，改文案+改内容）；「审核不通过/删除下线」才是重做。

**官方规则**（《职业资质认证2.0规则》`bilibili.com/opus/659053218220736529`）：
财经领域 7 条路径**每一条**都要身份证+手持照+从业资格+**一个月内盖章《在职证明》**
（且在职单位需与金融领域相关）。外加三条决定性限制：
「**暂不支持企业或机构申请**」「**暂不对虚拟UP主开放**」「**财经领域不支持模糊外显**」
（须明示工作单位+科室/职位+**个人真实姓名**+登记编码）。

**🚨 为什么「去认证」是死路**：① 没有金融在职身份，拿不出在职证明
② 「稿件为企业/机构性质则不予认证」③ 财经必须公开真实姓名 → **与脱敏铁律正面冲突**
④ 资质对应的正是荐股/带单那条我们不走、也不能走的路。

**✅ 正解：把内容从「财经主体」重写成「工程主体」**（改包装与剪辑，**不改内核**）：
标题去强领域词（「手搓一个**XX 台**」→「手搓一个**XX 终端**」）·
简介换工程语言（数据管道/连续序列拼接/表达式搜索/事件驱动）·
画面多给代码/日志/架构图、少给 K 线特写与盈亏面板 · 分区投科技·编程而非财经商业。

💎 **我们的选题本来就全是工程题**（这是优势不是将就）：EP2 成交假设 = 事件驱动引擎的成交时点 ·
EP3 拼接 = 连续序列拼接 · EP4 六项体检 = 可信度工程 · EP5 因子 = 表达式搜索。

**两条不能想当然的**：
- **图文 ≠ 视频** —— 实测长文平台零门槛运营该领域内容，红线只有「禁联系方式/禁推销/禁非法荐股」，
  **没有「必须持金融从业资质才能发」**。视频被卡 ≠ 内容线死。
- **换平台 ≠ 绕开监管** —— 「禁止非财经认证账号发布财经专业内容」是各平台通行口径。
  真正的变量是**内容主体**，不是平台。

### 🚨 脱敏自检的两个坑（本次都撞了）

1. **文档里写的检查命令本身会被判 BLOCK** —— 把绝对路径抄进「发布前必跑」→
   `check_leak.py` 扫这份文档报 3 处 BLOCK、退出码 1，**但不在文案正文里**。
   → 文档里写**占位命令**（`check_leak.py <本目录> --quiet`），**对外文档和工作稿一样要脱敏**。
2. **`~` 在 agent 环境里展开成沙箱 home，不是真 home** —— `~/.<agent-dir>/skills/...`
   → 指向一个**不存在的目录**。
   → **调用 skill 里的脚本一律用绝对路径**，别用 `~` 展开。（pitfalls E1 的变体，E1 讲凭据文件/`$HOME`，这条讲脚本路径。）

完整方法（文案三件套 / 简介骨架 / 章节时间戳推导脚本 / 平台门槛一手取证 /
三条路 + 我的判断 / 回复形状 / 反面模式）
→ **repo 的 `docs/publishing-and-platform-gating.md`**。

---

## 跨框架适配

这套管线**不绑定任何 agent 框架**。所有与运行时环境相关的解析集中在
`scripts/agentenv.py`，靠**优先级链**逐个尝试。

| 环境变量 | 作用 | 回退 |
|---|---|---|
| `TUTORIAL_PYTHON` | 跑管线的解释器 | `$PY` → agent home 下的 venv → `python3` |
| `TUTORIAL_ENV_FILE` | 凭据文件 | `$AGENT_HOME/.env` → `~/.env` |
| `AGENT_HOME` | agent 运行时根目录 | — |
| `TUTORIAL_SKILL_HOME` | skill 根目录 | 脚本自身位置上溯两级 |

🚨 **别用 `Path.home()`** —— agent 沙箱常把 `$HOME` 劫持成自己的沙箱家目录，
真正的家目录和凭据文件在别处 → 静默拿不到 key，不报错，最难查。
`agentenv.real_home()` 用 `pwd.getpwuid()` 直接问操作系统要，不受 `$HOME` 影响。

🚨 **完整 chromium 和 headless_shell 是两回事** —— `record_video` 的 screencast
在 headless_shell 上不支持。`find_chromium()` 显式排除 headless_shell 目录。

---

## 🚨 改完管线必须跑自检

```bash
python scripts/selfcheck.py --verbose
```

**为什么必须有这个**：这套管线的错误**不抛异常**，而是变成画面里
「顶栏汇总 ≠ 明细之和」这种看起来还挺像回事的假数据。
肉眼发现不了，观众一眼看穿。

自检覆盖 13 项：路径解析、解释器探测、凭据查找、合成守卫、hex 颜色守卫、
**adapter 汇总自洽性**（明细之和 == fix_summary 算出的 total_amount）、
单价反解精度、route 命中判定、报价确定性、领域配置完整性、模板合法性、扫描器可运行。

`install.sh` 跑完依赖检查会自动跑一遍。

---

## 目录结构

```
work/
├── project.json      配置(含 visual 段: 标题卡/推镜/框选; endcards 段; music 段)
├── script.json       旁白文案（[{title, keyword, card_sub, text, focus[]}...]）
├── align.json        ASR 强制对齐轴（asr_align.py 产出 → 字幕时间轴的来源）
├── token.txt         access token（可选，登录后注入 localStorage）
├── token2.txt        refresh token
├── audio/segNN.mp3   旁白（tts.py 产出）
├── raw/segNN.webm    录屏分段（recorder.py 产出）
├── out/
│   ├── vNN.mp4       对齐后的分段(字幕已烧进这里)
│   ├── cardN.mp4     标题卡片段(静音, 插在段落前)
│   ├── intro.mp4     片头片段(endcard.py 生成)
│   ├── outro.mp4     片尾片段(endcard.py 生成)
│   ├── final.mp4     横屏成片(**已含字幕+片头片尾+配乐**, 交付物就是这个)
│   ├── compose_plan.json 每段的黑场/时长(排障用)
│   └── timeline.json 时间轴(标注片头/标题卡/段落/片尾 → 验收排除用)
```

---

## 常见问题速查

| 症状 | 多半是 | 看 |
|---|---|---|
| 画面像首页 / 像没登录 | 业务数据是空的 | pitfalls A2 / adapter-guide §3 |
| 顶栏和明细数字对不上 | 汇总字段硬编码 0 | pitfalls A4 |
| 任务卡片数值全是 0 | task_id 隔离 + 取不到合成价 | pitfalls A5 |
| 验证说干净但成片有泄漏 | 验证的不是成片 | pitfalls A1 / F2 |
| OCR 读出的数字和实际对不上 | 模型读错 / 没放大 | pitfalls A6 |
| 字幕烧录报 "No option name" | ffmpeg 没 libass | pitfalls D2 |
| 人声几乎听不见 | 没加 loudnorm | pitfalls D4 |
| 字幕断句怪 / 看起来重复 | 按字数硬折了 | pitfalls D2a |
| 成片没字幕但验收全过 | script_path 静默跳过 | pitfalls D2e / F5 |
| 段落比预期长 1.3s / 时间轴全乱 | `-t` 放成了输入选项 | pitfalls G1 |
| concat 报错 exit 69 / 流对不上 | `-map` 顺序 + 声道数不一致 | pitfalls G2 / G3 |
| concat 时 AAC 解码报错 | 音频 `-c copy` → extradata 不同 | pitfalls G4 |
| 高亮框没画出来 / 字幕大面积丢失 | `-map` 输入下标算错 | pitfalls G5 |
| 框有但标签牌被裁掉 | 推镜安全区没避开 | pitfalls G6 |
| 验收误报「成片没字幕」 | 标题卡区间没排除 | pitfalls G9 / G20 |
| **验收误报覆盖率 87.6%「没字幕」** | **只排除了 title_card, 漏了 intro/outro/段尾静音** | **pitfalls G20** |
| **字幕比人声慢半拍 / 越到片尾越偏** | **时间轴按字数摊(猜的)** | **pitfalls G16 / subtitle-pipeline §1** |
| **切句后整段变成一条字幕** | **对齐轴里没有标点** | **pitfalls G17** |
| **切条后有字丢失** | **两次切分之间用字数重新对齐** | **pitfalls G17** |
| **片头/片尾没声音** | **配乐从 0 秒开始, 但那 0~6s 是静音淡入** | **pitfalls G19** |
| **片尾配乐断掉 / 末尾 8 秒无声** | **串联两个 atrim 导致长度错乱** | **pitfalls G19** |
| **加了配乐但人声听不清** | **没做闪避(sidechaincompress)** | **pitfalls G18** |
| **加了配乐但整体响度 -35 LUFS** | **只做 alimiter 没做 loudnorm** | **pitfalls G18** |
| **片头装饰元素比标题还抢眼** | **装饰圆太大太亮** | **endcard 评审清单(SKILL.md 片头片尾章)** |
| OCR 定位不到顶栏小字 | 原生分辨率读成乱码 | pitfalls G8 |
| 框定位不到却没报错 | 静默跳过 → 交付物看着正常其实没框 | pitfalls G10 |
| 成片 r_frame_rate=50/1（实际 25）| 帧数没取整 → concat 留半帧空洞 | pitfalls G14 |
| 看缩略图判断不出框准不准 | 缩略图看不清小字 | pitfalls G15 |
| 画面闷 / 观众划走 | 死画面，没做视觉层 | pitfalls G 组 / pacing-and-visual-spec.md |
| 灌第二个策略 UniqueViolation | 唯一约束不分策略 | pitfalls C1 |
| 拿不到 TTS key | `Path.home()` 被劫持 | pitfalls E1 |
| **调用 skill 脚本报文件不存在** | **`~` 展开成沙箱 home, 脚本要用绝对路径** | **publishing §5.2** |
| **发布后「流量受影响」/ 被判财经金融专业领域** | **视频形态在财经领域被卡, 认证是死路** | **publishing §4** |
| **章节时间戳靠估, 观众按它跳就跳错** | **要从 timeline.json 推导** | **publishing §2** |
| **工作稿自己被判 BLOCK / 退出码 1** | **文档里写了本机路径, 要用占位命令** | **publishing §5.1** |
| TTS 生成的文件 ffprobe 报坏 | 返回的是 hex 不是 base64 | pitfalls E3 |
| **配乐片段选哪个位置，全靠"能量占比%"** | **被带宽骗了, 宽带音乐占比恒≈带宽/奈奎斯特** | **pitfalls G37** |
| **AST 跑纯音乐片段 Speech=0.000** | **正常, 候选里本来没人声** | **pitfalls G38** |
| **片头不够激昂 / 像"闷"** | **不是加音量, 要冲击瞬态 + 词间回弹** | **pitfalls G33** |
| **片头开头在"渐入"而不是"炸开"** | **切入点要预滚到重拍前 0.25s** | **pitfalls G39** |
| **片头音乐像断了 / 一直很闷** | **闪避把音乐压死了, 要门控触发信号** | **pitfalls G40** |
| **算出 SNR 是负数, 以为旁白被糊** | **要扣除音乐贡献, 别直接比干声** | **pitfalls G41** |
| **豁免闪避后旁白还是被糊** | **冲击要快速回落, 不能全程满能量** | **pitfalls G42** |
| **whisper 转写一致度低, 疑似旁白糊** | **用拼音序列比, 别用繁简映射表** | **pitfalls G43** |
| **换了激昂段落后旁白被压** | **人声带占用没查, 闪避参数没配套调** | **pitfalls G34** |

---

## 配套

- **视频后期管线**（字幕烧录 / BGM 下载识别选段混音 / 成片验收 / 交付脱敏）→ 同 repo 的 skill `tutorial-video-post`
  → 本 skill 负责**录屏到无声底版**；录完拿到 silent.mp4 之后，**后期走那个 skill**
  （尤其是 BGM：那边有 select_segments + mix_bgm，激昂/选段/闪避豁免都是最新的）。
- **成片脱敏扫描** → 本 skill 自带 `scripts/scan_leak.py`
  （针对视频场景：抽帧 OCR + 音轨转写 + 文本全扫）。
- **系列规划 / 发布门槛** → repo 的 `docs/series-planning.md` 与 `docs/publishing-and-platform-gating.md`。
