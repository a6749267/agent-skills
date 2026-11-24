---
name: tutorial-video-post
description: >
  视频后期制作管线 —— 字幕烧录 + BGM 下载/识别/选段/混音 + 成片验收 + 交付脱敏。
  触发语：「做视频后期」「加字幕」「配BGM」「配乐」「配乐太闷」「片头不够激昂」
  「片头一打开就是高潮」「下载音乐」「选曲」「选什么曲子」「BGM 盖住人声」
  「人声听不清」「音乐识别」「音频验收」「成片脱敏」「发布前检查」
  「做视频的完整流程」「视频后期打包」「字幕烧录」「字幕对不上」
  核心资产: scripts/ 里的 fetch_music(下载) / music_identify(识别) /
  select_segments(选段) / mix_bgm(混音) / subtitle_post(字幕) /
  verify_audio(验收) / scan_leak(脱敏) / check_tofu(豆腐块AST扫描)。
  配套踩坑手册 references/pitfalls.md（按症状分 A–G 七组：选曲/混音/选段/字幕/脱敏/画面绘制/脚本工程）、
  配乐方法论 references/bgm-methodology.md、
  字幕配方 references/subtitle-recipe.md、
  素材获取与自检 references/sourcing-and-selfcheck.md。
---

# 视频后期制作管线

把「一条录好的无声底版」变成「可以直接发出去的成片」。

```
无声底版 silent.mp4 (画面+旁白)
      │
      ├─ ① 字幕    subtitle_post.py  → 切句/排版/烧录/覆盖率验收
      │
      ├─ ② 下载    fetch_music.py    → 按动感 genre 抓候选曲池
      ├─ ③ 识别    music_identify.py → AST + CLAP + 可听要素 打标签
      ├─ ④ 选段    select_segments.py→ 三维交叉验证挑 intro/body/outro 取材点
      ├─ ⑤ 配乐    mix_bgm.py        → 分段动态混音 + 冲击包络 + 词间回弹
      │
      ├─ ⑥ 验收    verify_audio.py   → whisper 清晰度 / 旁白音乐比 / 冲击 / 帧完整性
      └─ ⑦ 脱敏    scan_leak.py      → 文本全扫 + 视频抽帧 OCR + 音轨转写
```

---

## 🚨 先读这一条：激昂 ≠ 加音量

用户说「片头不够激昂 / 太闷」时，**加音量是错解**。

真正让片头炸起来的是三件事（缺一不可）：

| # | 要素 | 做法 | 判据 |
|---|---|---|---|
| ① | **瞬态冲击** | 段首**直接满能量切入**（不做静默蓄势），且切入点预滚到重拍前 0.25s | 首 100ms 就是满能量 |
| ② | **词间回弹** | 闪避 `release` 380ms → **160ms** | 口播一停，音乐 160ms 内顶回来 |
| ③ | **段落抬升** | intro/outro 增益比 body 高 10~13dB | 层次实测 |

**为什么不能加音量**：音乐一大就把旁白压死。实测「片头比正文高 26dB」→ AST Speech 掉到 0.18（完全听不清）。

**关键平衡**：冲击要**快速回落**，不能全程满能量。
t=0 满能量 → 0.3s 内落到 −13dB 垫底 → 闪避接管。
实测旁白/音乐比：豁免无衰减 **+23.4dB** → 加衰减 **+32.5dB**（+9.1dB）。

详见 `references/bgm-methodology.md`，坑位见 `references/pitfalls.md` A/B/C/E 组。

---

## 五步走

```bash
SKILL=<本 skill 目录>          # ⚠ 不要用 ~ 展开(见下"路径陷阱")
PIP=/path/to/venv/bin/python  # 需要 torch / transformers / numpy / faster_whisper / pypinyin

# ── ① 字幕 ──
$PIP $SKILL/scripts/subtitle_post.py --font-check          # 先自查字体(会漏豆腐块)
$PIP $SKILL/scripts/subtitle_post.py \
    --align align.json --script script.json --timeline timeline.json \
    --video silent.mp4 --out with_subs.mp4

# ── ② 下载 ──
$PIP $SKILL/scripts/fetch_music.py --out music_pool \
    --genres techno,house,edm,drum-and-bass --per-genre 20

# ── ③ 识别 ──
$PIP $SKILL/scripts/music_identify.py music_pool/*.mp3 --out music_pool/analysis.json

# ── ④ 选段 ──
$PIP $SKILL/scripts/select_segments.py music_pool/best.mp3 \
    --intro-len 24.36 --outro-len 22.60 --json segments.json

# ── ⑤ 配乐 ──
$PIP $SKILL/scripts/mix_bgm.py --bgm music_pool/best.mp3 \
    --silent with_subs.mp4 --timeline timeline.json --out final.mp4
#   (从 segments.json 抄 SRC 填进 mix_config.json 可自定义)

# ── ⑥ 验收 ──
$PIP $SKILL/scripts/verify_audio.py final.mp4 \
    --silent silent.mp4 --timeline timeline.json

# ── ⑦ 脱敏 ──
$PIP $SKILL/scripts/scan_leak.py publish_copy.md          # 文本全扫
$PIP $SKILL/scripts/scan_leak.py final.mp4 --ocr --transcribe
#   你的私有词(项目名/内部代号/自有域名)放 assets/leak_patterns.json —— 脚本不硬编码任何人的私密身份
#   $PIP $SKILL/scripts/scan_leak.py --patterns my_patterns.json publish_copy.md
```

### 🚨 依赖装哪（按脚本分开，不是所有脚本共用一个 venv）

| 脚本 | 需要的包 | 说明 |
|---|---|---|
| `fetch_music.py` | 标准库 | 零依赖 |
| `select_segments.py` | numpy | 只要 numpy |
| `mix_bgm.py` | 标准库 + ffmpeg | 零依赖 |
| `subtitle_post.py` | pillow | 字体渲染 |
| `scan_leak.py` | 标准库（可选 faster_whisper / PyObjC） | 零依赖可跑 |
| `music_identify.py` | torch + transformers + numpy | AST/CLAP |
| `verify_audio.py` | numpy（可选 faster_whisper + pypinyin） | 帧检查零依赖 |

**实测各 venv 的内容**（不同环境的包不一样，别假设）：
```
/tmp/venv_audio/bin/python        → torch + transformers + numpy      ✅ AST/CLAP
<agent 自身 venv>/bin/python       → numpy + faster_whisper + pypinyin  ✅ 验收
                                     （无 transformers → AST/CLAP 跑不了）
```
→ **AST/CLAP 和 whisper 需要两个不同的解释器**，或自己建一个全装的 venv。
→ 找 venv：`python -c "import transformers"` / `python -c "import faster_whisper"` 逐个试。

`HF_ENDPOINT=https://hf-mirror.com` —— 直连 huggingface.co 在大陆会超时。

### 🚨 路径陷阱（必读）

**`~` 在 agent 沙箱里展开成沙箱 home，不是真 home** → `~/.<agent-dir>/skills/...` 指向一个**不存在的目录**。

→ **调用 skill 里的脚本一律用绝对路径**，别用 `~`。
→ **文档里写绝对路径会被脱敏扫描判 BLOCK** → 对外文档一律用**占位命令**（如 `<本 skill 目录>`）。

---

## 各环节要点

### ① 字幕

**时间必须来自 ASR 强制对齐，不能按字数摊**（摊的时间是猜的，TTS 语速不均，每段偏 0.3~1.5s）。

**文字必须取脚本原文**（ASR 中文同音字错很多：终端→中端、这一屏→这一瓶）。

**断句必须在标点处**：按字数硬折会出现「…是一个模块」/「一个模块搭起来的」这种视觉重复。
→ 标点优先于长度：在标点候选里选**让最长行最短**的那个。

**`.ttc` 字体必须显式指定 index**：
- `STHeiti Medium.ttc` → index **1** = 简体（index 0 是繁体）
- `Menlo.ttc` → index **0** = Regular（不是 Bold）

**字体路径传错类型会静默回落成豆腐块**：传函数而不是 str → `ImageFont.truetype` 抛异常 → 被 `except` 吞掉 → 满屏缺字方框且**不报错**。→ 必须断言。

**验收覆盖率必须排除四种无字幕区间**：`title_card` / `intro` / `outro` / **段尾静音尾巴**。
只排除 title_card 会误判成「成片没字幕」。

### ② 下载音乐

**候选池的 genre 决定了成败**。
只抓 epic/cinematic/technology/upbeat/trailer/dramatic → 全是「电影感/企业科技」→ 选出来的必然是 corporate 励志垃圾。

→ 动感池：`techno / house / drum-and-bass / electronic / trance / edm / dubstep / breakbeat / funk / rock / hip-hop / bass / groove / synthwave`
→ 排除：`cinematic / ambient / chillout / corporate / sad`

抓取要**限速**（每 genre 间隔 ≥1s，429 要退避）。

⚠ **该曲库的列表页不认 `?page=` 参数**（实测 page=1/2/3 内容完全一样）→ 每页只暴露首批曲目。
→ **靠多 genre 并集扩池子**（16 个 genre × 5 首 ≈ 80 首去重后），别指望翻页。
→ 同一 id 在多 genre 间重复 → 脚本已做**全局去重**。

### ③ 音乐识别

**频谱图回答不了「激不激情」** —— 高端 Corporate 配乐和真带劲的电子乐频谱几乎一样，视觉模型都给高分。

三个独立证据源：

| 源 | 判据 |
|---|---|
| **AST**（AudioSet 527 类） | 出 `Techno/House/Bass drum/Drum machine` = 带劲；出 `Violin/Cello/Sad/Ambient` = 排除 |
| **CLAP**（零样本音频-文本） | margin = 正向提示相似度 − 负向提示相似度；**margin < 0 = 比 corporate 垃圾还像垃圾** |
| **可听要素** | 鼓点密度（起伏次数太少 = 墙纸）、瞬态锐度、低频冲击、动态范围 |

⚠ **STFT 窗长必须 >> hop**：用 hop=10ms 同时当 FFT 长度 → 分辨率 100Hz/bin → 20-200Hz 只有 1 个 bin → kick 能量全为 0。正确：窗长 2048~4096，hop 512。

⚠ AST 跑在**纯音乐片段**上时 `Speech=0.000` 是正常的（候选里本来没人声）—— 别误读成「AST 判不了」。

### ④ 选段（最容易被跳过的环节）

🚨 **这一步不能跳。** 实测：`mix_bgm.py` 用默认 `src=0.0` → 音乐正好落在曲子的**静音淡入区**
→ 纯音乐轨首 50ms 只有 **−44.8dBFS**（应该 −30 左右）→ 开头没有高潮。
→ **必须先跑 `select_segments.py` 拿到 cut 值再混。**

**必须三维交叉验证**，任何一个单独用都会选错：

| 维度 | 指标 | 判据 |
|---|---|---|
| 人声带占用 | 300–3400Hz **功率密度** dB（相对宽带平均） | 越**低**越好（不压旁白） |
| 低频冲击 | 55–120Hz 功率密度 dB | 越高越好（有"砰"） |
| 激昂度 | AST 冲击标签命中 + Music 分 | 4/4 命中 |

🚨 **千万别用「频带能量占比%」**：宽带音乐幅度谱几乎是平的，占比 ≈ 带宽/奈奎斯特 = 3100/8000 = **38.75%**，跟内容无关 → 所有候选都落在 40~52%，毫无区分度。
→ 必须用**功率密度 + 等带宽归一**。

🚨 **片头切入点要预滚到重拍前 0.25s**，不是切在拍点上。
根因：鼓组的 kick 是在**落点**才砸下的，落点前 200ms 是起音期 → 切在落点 → 开头是「浮起来」，不是高潮。
实测：切落点首 100ms = −11.8dBFS（爬升） vs 切落点−0.25s = **−8.23dBFS**（满能量）。

⚠ **「满能量」的判据是首 100ms 的绝对电平**（相对曲子满能量基准），
**不是**「首 vs 后续」的差值 —— 差值大反而说明切入后音乐马上变安静（踩在弱位上）。
脚本用 `head_fill`（= 首100ms − 全曲RMS的95分位）表达，越接近 0 越好。

三段取材点要**互不重叠**（否则听到同一段旋律）。

### ⑤ 配乐

**三条铁律**：
1. `loudnorm` **只作用于口播轨** —— 放末端会把三段相对关系压平
2. **有旁白的段一律要闪避** —— 但片头前 0.5~0.8s 要**豁免**（否则音乐被压死）
3. 终混末端只留 `alimiter`

**闪避豁免只能门控触发信号 `[sc]`**（`asplit` 的触发副本），
**绝不能动 `[va]`**（真正的声音）→ 否则旁白被静音。

**包络用一条 `volume` + `eval=frame` 表达式**算完 —— 两个 `volume=enable=` 串联时，enable 关闭会**跳过整个滤镜** → 音量回到 0dB（全段最大）→ 完全错。

**满电平硬切会有 digital click** → 段首加 30ms 斜坡（不到 1 帧，人耳听不出，只消爆音）。

**串联两个 `atrim` 会让配乐末尾凭空消失** → 用**单个 atrim**。

### ⑥ 验收

四道必跑（全部客观可复现）：

| 检查 | 工具 | 判据 |
|---|---|---|
| 旁白清晰度 | whisper 转写成片 vs 干声，**拼音序列**比 | ratio ≥ 0.85 |
| 旁白/音乐比 | musiconly 轨反推音乐贡献后算残差 | ≥ +20dB |
| 冲击包络 | musiconly 轨首 50ms vs 后续 | 差值小（满能量切入） |
| 帧完整性 | ffprobe 所有 pts 间隔 | 恒为 1/fps，无空洞 |

🚨 **whisper 相似度用拼音序列**，别用繁简映射表（不完整就崩）。
🚨 **窗口 ≥4s 且 beam_size=5** —— 3s+beam1 会让同一段转写随机抖动，拿抖动当音频问题会误判。
🚨 **SNR 要扣除音乐贡献** —— 直接拿成片比干声会漏掉 loudnorm 增益，算出负 SNR 的假警报。

### ⑦ 脱敏

**文本类物料**（文案/简介/标题/标签）→ 全量扫。
**视频/音频 → 不能扫二进制**（压缩码流里的随机字节会被误判成敏感词，几百处假警报）→ 要**抽帧 OCR + 音轨转写**。

发布前必查：编号代码 / 对象名称 / 账户 / 明细 / 金额 / 资金 / 阈值参数。

---

## 🚨 交付物 = 成片文件本身

**视频类任务的交付物是那个 mp4 文件。**
把成片路径直接放进回复（`MEDIA:<绝对路径>`），或立刻 `send_message` 带 `MEDIA:` 发出去。
「已完成 + 路径在某某目录」这种交付在对话里等于没交付。

**报告可以写，但成片必须在同一条消息里给到。**

---

## 目录结构

```
work/
├── script.json          旁白文案 [{title, keyword, card_sub, text, focus[]}...]
├── align.json           ASR 强制对齐轴（时间唯一正确来源）
├── timeline.json        时间轴 {total, tracks:[{kind,seg,start,end}]}
├── silent.mp4           无声底版（画面+旁白，无字幕无音乐）
├── music_pool/          候选曲池 + manifest.json + analysis.json
├── segments.json        选段结果 {intro:{cut,...}, body:{}, outro:{}}
├── mix_config.json      混音参数覆盖
├── final.mp4            ★ 成片（字幕+配乐都在这里）
└── final_musiconly.wav  纯音乐轨（验收用）
```

**timeline.json 的 `kind` 取值**：`title_card` / `intro` / `segment` / `outro`。
`mix_bgm.py` 靠它定位三段边界；`subtitle_post.py` 靠它排除无字幕区间。

---

## 常见问题速查

| 症状 | 多半是 | 看 |
|---|---|---|
| 配乐像「垃圾配乐」/  corporate 励志 | 候选池 genre 错了 | pitfalls A1 |
| 频率图和频谱都一样, 选不出 | 频谱图回答不了激不激情 | pitfalls A2 |
| 人声几乎听不见 | 没做闪避 / 闪避范围搞错 | pitfalls B1 / B2 |
| 人声被音乐压死但闪避做了 | 片头旁白从 0.1s 就开始说话 | pitfalls B3 |
| 片头音乐像断了 | 闪避把音乐压死了 | pitfalls B3 |
| 豁免闪避后旁白还是糊 | 冲击没快速回落 | pitfalls B4 |
| 片头开头在「渐入」不是「炸开」 | 切入点没预滚到重拍前 | pitfalls B5 |
| 算出 SNR 是负数 | 没扣除音乐贡献 | pitfalls B6 |
| whisper 一致度低疑似旁白糊 | 该用拼音序列比 | pitfalls B7 |
| 音乐选哪个位置全靠「占比%」 | 被带宽骗了 | pitfalls C1 |
| 三段音量关系被压平 | loudnorm 放在末端 | pitfalls C2 |
| 配乐末尾凭空消失 | 串联两个 atrim | pitfalls C3 |
| 配乐只在正文段被闪避 | 有旁白的段都要闪避 | pitfalls B1 |
| 字幕比人声慢半拍 | 时间按字数摊 | pitfalls D1 |
| 字幕断句怪 / 看起来重复 | 按字数硬折 | pitfalls D2 |
| 满屏豆腐块 | 字体路径传错类型静默回落 | pitfalls D3 |
| 繁体字形 / 字体 index 错 | .ttc 没指定 index | pitfalls D4 |
| 验收误报「成片没字幕」 | 没排除段尾静音尾巴 | pitfalls D5 |
| 成片被扫出几百处 BLOCK | 扫了 mp4 二进制 | pitfalls E1 |
| 文档自己被判 BLOCK | 文档里写了绝对路径 | pitfalls E2 |
| 调用 skill 脚本报文件不存在 | `~` 展开成 agent 的 home, 不是真 home | pitfalls E3 |
| **AST/CLAP 报"没有 transformers"** | **用的 venv 只装了 whisper 系, 换一个** | **SKILL.md 依赖表** |
| **识别脚本判 ❌ 但没跑成** | **分析失败 ≠ 不合格, 看 verdict 有没有"未跑"** | **pitfalls A5** |
| **动态范围报 175dB** | **静音段把 min 拉爆, 只统计有声段** | **pitfalls A6** |
| **曲库只抓到 5 首 / 翻页无效** | **列表页不认 ?page=, 靠多 genre 并集** | **pitfalls A7** |
| **CLAP 报"no attribute 'norm'"** | **transformers 新版返回 ModelOutput** | **pitfalls A8** |
| **开头有"啪"的爆音** | **满电平硬切 → 加 30ms 防 click 斜坡** | **pitfalls B8** |
| **点击判据报几千处 click** | **真 click 要三条同时满足(见 pitfalls)** | **pitfalls B8** |
| **选出的切入点听着不炸** | **满能量判据是绝对电平不是差值** | **pitfalls B8b** |
| **两个 volume=enable= 串联后整段变响** | **enable 关闭会跳过滤镜 → 音量 0dB** | **pitfalls B9** |
| **片尾泄气** | **收束前先冲高再落** | **pitfalls B10** |
| **扫描器把自己判成 BLOCK** | **规则表要逐行加 leak-ok 豁免** | **pitfalls E5** |
| **成片被扫出几百处 BLOCK** | 扫了 mp4 二进制 | pitfalls E1 |
---

## 配套

- **录屏管线**（录屏 + 合成 + 拦截 + 数字自洽 + 视觉层）→ 同 repo 的 skill `tutorial-video-recorder`
  → 录完得到 silent.mp4 后，**后期走本 skill**。
- **成片脱敏扫描** → 本 skill 自带 `scripts/scan_leak.py`
  （针对视频场景：抽帧 OCR + 音轨转写 + 文本全扫）。
- **系列规划 / 发布门槛** → repo 的 `docs/series-planning.md` 与 `docs/publishing-and-platform-gating.md`。

### 🚨 两个 skill 的职责边界

| 环节 | 归谁 |
|---|---|
| 录屏 / 合成 / 拦截 / 数字自洽 / 视觉层 / 章节卡 | `tutorial-video-recorder` |
| **字幕切句排版烧录 / BGM 下载识别选段混音 / 成片验收 / 交付脱敏** | **本 skill** |
| 发布文案 / 平台门槛 | repo 的 `docs/publishing-and-platform-gating.md` |

> 本 skill 里的 `mix_dynamic_music.py` / `verify_music_dynamic.py` 是**旧版**（v6 时代），
> 新做配乐一律用本 skill 的 `select_segments.py` + `mix_bgm.py`。
