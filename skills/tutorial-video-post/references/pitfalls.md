# 视频后期踩坑手册（按症状索引）

> 每条都是「症状 → 根因 → 解法」。所有数值都是实测来的，不是拍的。
> 配套脚本见 `scripts/`，方法论见 `bgm-methodology.md` / `subtitle-recipe.md`。

---

## A 组 —— 选曲阶段

### A1. 🚨🚨🚨 候选池 genre 错了 → 选出来的必然是 corporate 励志垃圾

**症状**：AST 打标签全是 `Cinematic` / `Dramatic` / `Ambient`，CLAP margin 为负，用户听完骂"垃圾配乐"。

**根因**：候选池只覆盖 `epic / cinematic / technology / upbeat / trailer / dramatic`
→ 全是「电影感/企业科技」，**一条 techno/drum-and-bass/house/edm 都没有**。

**解法**：
```bash
python scripts/fetch_music.py --out music_pool \
    --genres techno,house,edm,drum-and-bass,electronic,trance,dubstep,breakbeat,funk,rock,hip-hop
```
动感池：`techno / house / drum-and-bass / electronic / trance / edm / dubstep / breakbeat / funk / rock / hip-hop / bass / groove / synthwave`
排除：`cinematic / ambient / chillout / corporate / sad`

⚠ 抓取要限速（每 genre 间隔 ≥1s），429 要退避（脚本已内置）。

### A2. 🚨🚨 频谱图回答不了「激不激情」

**症状**：想用频谱图/视觉模型给候选曲打分挑"带劲"的，选出来被用户骂。

**根因**：高端 Corporate 配乐和真带劲的电子乐，频谱**几乎一样**（都是"高频延展 + 低频厚 + 有起伏"）。频谱图只能回答"有没有能量"，回答不了"是什么音乐"。

**解法**：用真模型"听" → `scripts/music_identify.py`（AST + CLAP + 可听要素）。
```bash
python scripts/music_identify.py music_pool/*.mp3 --out music_pool/analysis.json
```
- **AST** → `Techno/House/Bass drum/Drum machine` = 带劲；`Violin/Cello/Sad/Ambient` = 排除
- **CLAP** → margin = 正向提示相似度 − 负向提示相似度；**margin ≤ 0 = 比 corporate 垃圾还像垃圾**
- **可听要素** → 鼓点密度（起伏次数太少 = 墙纸）、瞬态锐度、低频冲击、动态范围

⚠ **AST 跑在纯音乐片段上时 `Speech=0.000` 是正常的**（候选里本来没人声），别误读成"AST 判不了"。
AST 在这里的职责只有一个：**测这段音乐有多猛**（Music 分 + 冲击类标签命中数）。

### A3. 🚨 STFT 窗长必须 >> hop（低频分析的命门）

**症状**：kick / 低频冲击指标全为 0，频谱图的 LOW 面板渲染成空。

**根因**：用 `hop=10ms` 同时当 FFT 长度 → 频率分辨率 = SR/220 = **100Hz/bin** → 20–200Hz 只有 **1 个 bin** → 低频完全测不准。

**解法**：窗长 2048~4096，hop 512（重叠窗）→ 2048@22050 分辨率 **10.8Hz** → 20–200Hz 有 17 个 bin。

### A4. ⚠ 混完必须再做 loudnorm

只用 `alimiter` 实测只有 −35.3 LUFS（目标 −16）。
但 **loudnorm 只能做在口播轨上** —— 见 C2。

### A5. 🚨🚨 分析失败 ≠ 不合格

**症状**：识别脚本报「AST 冲击标签命中不足」，但 JSON 里 `ast_hot_ratio` 是 `null`。

**根因**：AST/CLAP 依赖 `transformers`，某些 venv 只装了 whisper 系 → 抛 ImportError 被 except 吞掉 → 指标是 `None` → **比较运算把它当成 0** → 误判"命中不足"。

**解法**：
- verdict 里先判断有无 `ast_error` / `clap_error` → 有就说「未跑(缺依赖)」，**不判 ❌**
- 装依赖：`torch + transformers`（AST/CLAP）与 `faster_whisper + pypinyin`（验收）**可能在不同的 venv 里**
- ⚠ 不同环境的包不一样，**别假设** —— 先 `python -c "import transformers"` 确认

### A6. 🚨 动态范围不能直接用 max−min

**症状**：动态范围报 175dB（正常曲子在 10~30dB）。

**根因**：曲子里的**静音淡入段**把 RMS 的 min 拉到 −100dB → 差值虚高。

**解法**：只统计**有声段**：
```python
audible = db[db > db.max() - 30.0]     # 只留有声段(阈值 45 太松, 实测仍虚高到 44dB)
dyn = audible.max() - audible.min()
```
实测修正后：175dB → **29.8dB**（正常电子乐范围）。

### A7. ⚠ 该曲库的列表页不认 ?page= 参数

实测 `?page=1/2/3` 返回的 HTML 完全一样 → 每页只暴露首批曲目（实测 5 首）。

→ **靠多 genre 并集扩池子**，别指望翻页。
→ 同一 id 在多 genre 间会重复 → **全局去重**。

### A8. 🚨 transformers 新版 API 改名 → CLAP 静默失败

**症状**：`clap_error: 'BaseModelOutputWithPooling' object has no attribute 'norm'`。

**根因**：两个改动叠加：
1. `proc(audios=...)` 改名成 `proc(audio=...)`
2. `get_audio_features()` / `get_text_features()` 返回 **ModelOutput** 而不是 Tensor → 要取 `pooler_output`

**解法**：
```python
inp = proc(audio=x, sampling_rate=SR_CLAP, return_tensors="pt")
ae = model.get_audio_features(**inp)
if not isinstance(ae, torch.Tensor):        # 新版
    ae = getattr(ae, "pooler_output", None) or ae[0]
ae = ae / ae.norm(dim=-1, keepdim=True)
```
⚠ **写脚本时不要照抄文档里的旧 API** —— 版本一变就静默失败（被 except 吞掉 → 只在 JSON 里留一行 error）。
→ 每次改完识别脚本，**看 JSON 有没有 `clap_error` 字段**，别只看 stdout。

---

## B 组 —— 混音阶段

### B1. 🚨🚨🚨 闪避没做 / 闪避范围搞错 → 音乐盖死旁白

**症状**：AST Speech 掉到 0.18，用户听不清解说。

**根因**：
- 完全没做闪避
- 或者只做了正文段、片头直通（v6 时代的假设：片头无旁白）→ **但 v9/v11 的片头是有旁白的**（0~24s 全是解说）

**解法**：**先确认每段有没有旁白**，再决定闪避范围。有旁白的段一律要闪避。
参数参考：
```
body : threshold=0.015 ratio=12 attack=4  release=200
intro : threshold=0.02  ratio=14 attack=3  release=160
outro: threshold=0.02  ratio=14 attack=3  release=160
```

### B2. 🚨 release 380ms → 音乐一直被压着，听起来「闷」

**症状**：做了闪避，但音乐"闷"，没有推力。

**根因**：`release=380ms` 意味着口播停了之后，音乐还要 380ms 才顶回来 → 整段都像被压着。

**解法**：`release` 缩到 **160ms** → 词与词之间音乐"顶出来"，有呼吸感。

⚠ 副作用：音乐顶得更高 → 口播可能被压。
→ 用**更低的 threshold + 更高的 ratio** 补偿（保证说话瞬间压得够狠）。

### B3. 🚨🚨🚨 闪避会把片头音乐压死（旁白从 0.1s 就开始说话时必踩）

**症状**：混完的纯音乐轨前 1.5s 全是 **−44~−50dB**，音乐像"断了"。成片开头很闷。

**根因**：片头旁白从 **0.127s** 就开始说话 → 闪避立刻把音乐压到底。
对照：未混音的源在取材点处是 **−11.8dBFS 满能量** → **是闪避压的，不是源的问题**。

**排查顺序**（照着走，别猜）：
1. 抽**纯音乐轨**（口播权重 0）看是不是真断
2. 单独跑 `[bgm]volume=...:eval=frame` 看滤镜本身
3. 跑**完整滤镜链**看链路
4. 都正常 → 就是闪避压的

**解法**：**只门控触发信号 `[sc]`**，让片头前 0.5~0.8s 旁白不触发闪避。
```python
fc.append(f"[sc]volume='if(lt(t,{gate}),0,if(lt(t,{gate+r}),(t-{gate})/{r},1))'"
          f":eval=frame[scg]")
```
⚠ **只能门控 `[sc]`**（`asplit` 的触发副本），**绝不能动 `[va]`**（真正的声音）→ 否则旁白被静音。
⚠ 门控用 **ramp** 而非硬切，否则闪避突然咬合产生"咔"声。
⚠ `[sc]` 的 t 是**全局时间轴** —— 片头 `delay_ms=0` 时全局 t == 局部 t，门控才对得上。

### B4. 🚨🚨 豁免闪避后音乐全程满能量 → 旁白被糊

**症状**：旁白/音乐比只有 **+23dB**，第一句"不是自欺欺人"被糊掉。

**解法**：**冲击 + 快速回落** —— t=0 满能量 → 0.3s 内落到 −13dB 垫底 → 闪避接管。
```python
env = (f"if(lt(t,{fall}),1+({dbv}-1)*(t/{fall}),"
       f"if(lt(t,{gate}),{dbv},"
       f"if(lt(t,{gate+r}),{dbv}+(1-{dbv})*(t-{gate})/{r},1)))")
```
实测：旁白/音乐比 **+23.4dB → +32.5dB**（+9.1dB），而 t=0 仍是满能量冲击。

⚠ 衰减包络要作用在**音乐轨**（闪避输入端），不是触发信号。
⚠ **量音乐包络必须看 musiconly 轨**，看成片会被旁白主导而误判。

### B5. 🚨🚨🚨 "一打开就是高潮" = 切入点要预滚到重拍前

**症状**：片头开头是"浮起来"，不是高潮。

**根因**：切在重拍落点时，**鼓组的 kick 是在落点才砸下的**，落点前 200ms 是起音期 → 音乐在渐入。

**解法**：**切入点 = 落点 − 0.25s**，让冲击正好砸在 t=0。
扫描 13 个重拍 × 8 种预滚 → **预滚 0.25s 是所有落点的共同最优**（10/13 命中）。

实测：切落点首 100ms = −11.8dBFS（爬升） vs 切落点−0.25s = **−8.23dBFS**（满能量）。

⚠ onset 检测用**谱通量**（spectral flux），别用 RMS 差分。
⚠ 选点要**三重验证**：人声带 dB（最干净）/ 冲击 dB（最高）/ 首 100ms 满能量。

### B6. 🚨🚨 带内 SNR 要扣除音乐贡献

**症状**：算出 SNR **−3~−7dB**（音乐比人声强），差点误判"旁白被糊"。

**根因**：那个 SNR 是「成片带内能量 / **干声**带内能量」，但**成片里的旁白经过了 loudnorm 增益**（干声 −25.85dBFS → 成片 −18 LUFS），比值自然变负。

**解法**：用 **musiconly 轨**反推音乐在成片里的贡献，再算残差：
```python
mus = band(musiconly) * (BG_WEIGHT ** 2)     # musiconly 已含 amix 权重
voice = max(band(mix) - mus, 1e-12)
snr_db = 10 * np.log10(voice / mus)
```
⚠ **旁白/音乐比 > +20dB 就算旁白清晰**（不是看 SNR 绝对值）。

### B7. 🚨 whisper 转写相似度用拼音序列

**症状**：同一段音频，成片和干声的转写相似度只有 0.692 → 误判"旁白糊了"。

**根因**：whisper 输出繁体 + 同音错字（手措/手搓）。繁简映射表不完整就崩，字符 Jaccard 会被标点拖垮。

**解法**：**转成拼音序列再比** —— 天然免疫繁简、同音错字、标点、空白：
```python
from pypinyin import lazy_pinyin, Style
def pinyin_seq(s):
    s = unicodedata.normalize("NFKC", s)
    out = []
    for p in lazy_pinyin(s, style=Style.NORMAL, errors=lambda x: x.lower()):
        p = "".join(c for c in p if c.isalnum())
        if p: out.append(p)
    return out
ratio = difflib.SequenceMatcher(None, pinyin_seq(a), pinyin_seq(b)).ratio()
```
⚠ whisper `transcribe()` 返回 **generator** → 要 `"".join(s.text for s in segs)`。
⚠ 窗口要 **≥4s 且 beam_size=5**：3s+beam1 时同一段音频的转写会随机抖动
（实测同一段成片/干声，3s/beam1 → ratio 0.250；10s/beam5 → 两边完全一致）→ **别拿抖动当音频问题**。

### B8. 🚨 满电平硬切 → 第 0 帧 digital click

**症状**：开头有"啪"的爆音。

**解法**：段首加 **30ms 斜坡**（不到 1 帧 = 40ms，人耳完全听不出淡入，只消爆音）。

⚠ **digital click 的正确判据**（三次栽在这上面，必须一次做对）：
只用「相邻样本最大跳变 > 0.03」会**大量误报**：
- hi-hat 瞬态：实测 **4000+ 处**假警报
- 源音乐自带的采样毛刺：实测安静段(−35dB)里 **3 处**假警报（后/前能量比仅 ±0.6dB）

✅ 完整判据 = 三条**同时**满足：
```python
# ① 孤立脉冲: 跳变后**回到原位**(|d[i]|>thr 且 |d[i+1]|>thr 且 |x[i-1]-x[i+1]|<0.01)
# ② 在**有声处**(跳变前 RMS > 噪声底)
# ③ 跳变后能量**明显下降**(post_e < pre_e * 0.7)
```
实测修正后：选段版 3 处假警报 → **0 真 click**；人工版同样 0。

### B8b. 🚨 "满能量切入" 的判据是**绝对电平**，不是"首 vs 后续"的差值

**症状**：按"首 100ms 比后续高多少"打分，选出了看似很炸、实际很弱的切入点。

**根因**：差值大恰恰说明**切入后音乐马上变安静**（踩在弱位上，或后面是 break）。
真实案例：`head_vs_rest=+4.07` 的点首 100ms 只有 −7.39dBFS；
`head_vs_rest=+2.17` 的点首 100ms 反而是 **−8.45dBFS**（更满）。

**解法**：用 `head_fill` = 首100ms电平 − 全曲 RMS 的 95 分位 → 越接近 0 越是满能量切入。

### B9. 🚨 两个 volume=enable= 串联 → 音量变 0dB（全段最大）

**症状**：加了区间增益后，整段都变响了，完全没分段。

**根因**：`enable` 关闭时**整个滤镜被跳过** → 音量回到 0dB（全段最大）→ 完全错。

**解法**：用**一条 volume + `eval=frame` 表达式**算完整包络。

### B10. 🚨 片尾收束要"冲高再落"，不能直接淡出

**症状**：片尾泄气。

**解法**：淡出开始前 1.2s 先抬 2.5dB，形成小推力，再收束 → 收得有张力。
```python
chain += f",volume='if(lt(t,{lift_st}),1,if(lt(t,{nd-fo}),1.19,0.84))':eval=frame"
```

---

## C 组 —— 选段与素材

### C1. 🚨🚨🚨 用"频带能量占比%"挑片段 = 被带宽骗了

**症状**：所有候选的占比都落在 40~52%，毫无区分度。

**根因**：**宽带音乐的幅度谱几乎是平的**，占比 ≈ 该带宽/奈奎斯特 = 3100/8000 = **38.75%**，**跟内容无关**，只反映带宽本身。

**解法**：算**功率密度**并做**等带宽归一**，用相对宽带平均功率密度的 dB：
```python
X = np.fft.rfft(x); P = (np.abs(X)**2)/len(x)
freqs = np.fft.rfftfreq(len(x), 1/sr)
avg_density = P.sum() / (freqs[-1] - freqs[0])   # 每 Hz 平均功率
band_density = P[(freqs>=lo)&(freqs<hi)].sum() / (hi-lo)
db = 10*np.log10(band_density / avg_density)
```
判据：**< 0dB** = 该带比平均频段安静（可当BGM底）；0~3dB = 偏亮；>3dB = 突出（会压旁白）。

实测同一首曲子：intro_26 = **−5.22dB**（最干净） / outro_196 = −3.59dB / outro_231（旧选）= **−0.14dB**（最脏）。

⚠ 用功率 `|X|²` 不是幅度 `|X|`，且**必须除以带宽** —— 不除就退化成上面那个坏指标。

### C2. 🚨🚨🚨 loudnorm 放末端 → 三段相对关系被压平

**症状**：片头比正文只高 2.4dB、片尾反而低 3.1dB → 分段设计全废。

**根因**：`loudnorm` 是**整片一次性归一化**，会把三段的相对关系压平。

**解法**：`loudnorm` **只做在口播轨上**，终混末端只留 `alimiter`。

### C3. 🚨🚨 串联两个 atrim → 配乐末尾凭空消失

**症状**：只输出 202.13s（应为 210.13s）→ 片尾没配乐。

**解法**：**单个 atrim**：`atrim=start=OFFSET:end=OFFSET+总长`，循环交给 `-stream_loop -1`。

### C4. ⚠ 三段取材点要互不重叠

否则 intro 和 outro 听到同一段旋律。

🚨 **症状**：选段输出里 intro 和 outro 的 `cut` **一模一样**。

**根因**：`pick()` 对 intro / outro 用不同权重打分，但**各自独立取最优** → 可能落在同一个点。

**解法**：outro 选取时**排除 intro 附近**的候选：
```python
gap = max(intro_len, outro_len) + 5.0
outro = pick(rows, ..., exclude_near=gap, exclude_at=intro["cut"])
```

### C5. ⚠ 曲子开头常是静音淡入

实测某曲 0~6s 是 −90dB → 片头会没声音。→ 取材点必须避开。

### C6. 🚨 选段只扫了曲子前 200 秒 → 漏掉后半段的高潮区

**症状**：选出的 outro 永远在曲子前半段。

**根因**：`onset_times(path, 0.0, 200.0)` 写死了扫描窗口，而**曲子后半段常常才是 outro 的好素材**。

**解法**：`scan_dur` 覆盖全曲（默认 400s）；`max_beats` 取最强的 N 个但**按时间排序**保留全曲分布
（否则只会在曲子开头扎堆）。

### C7. ⚠ body 筛选太严 → "(未找到)"

条件 `voice_band_db < -3.0 AND impact_db > 8` 且要避开 intro/outro 30s 内 → 常取不到。

→ 放宽到 `< -2.5 AND > 6.0`，并加**兜底**（`min(body_pool, key=voice_band)`）——
**宁可给一个次优点也别留空**。

---

## D 组 —— 字幕

### D1. 🚨🚨🚨 字幕时间必须用 ASR 强制对齐

**症状**：字幕比人声慢半拍，越到片尾越偏。

**根因**：按字数摊的时间是**猜的** —— TTS 语速不均匀，每段偏 0.3~1.5s，累积到片尾能偏好几秒。

**解法**：`scripts/subtitle_post.py --align align.json`（时间取 ASR，文字取脚本原文）。

⚠ **绝不能直接用 ASR 的识别文本当字幕** —— 中文同音字错很多（终端→中端、这一屏→这一瓶）。
⚠ 对齐轴里**没有标点** → 切句必须用脚本原文的标点，再映射回对齐轴。

### D2. 🚨🚨 断句必须在标点处

**症状**：「…是一个模块」/「一个模块搭起来的。」→ 视觉上像重复了。

**根因**：按字数硬折。

**解法**：**标点优先于长度** —— 在标点候选里选**让最长行最短**的那个，只要最长行 ≤ max_chars×1.25 就接受。

### D3. 🚨🚨🚨 字体路径传错类型 → 静默回落 → 满屏豆腐块

**症状**：满屏缺字方框，**没有任何报错**。

**根因**：写 `F(F, 25)` 而 `F` 其实是**函数**（不是字体路径）→
`ImageFont.truetype(<function>, 25)` 抛 AttributeError →
被 `except: return ImageFont.load_default()` 吞掉。

**解法**：断言「传进去的是 str 路径」，失败就**报错退出**，绝不静默回落。
```bash
python scripts/subtitle_post.py --font-check
```

### D4. 🚨🚨 .ttc 字体集合的 index 陷阱

- `STHeiti Medium.ttc`：index **0** = Heiti **TC**（繁体），index **1** = Heiti **SC**（简体）
- `Menlo.ttc`：index **0** = Regular（不是 Bold）→ 不能一律用 index=1

→ .ttc 必须显式指定 index，且不同字体不同。

### D5. 🚨🚨 验收覆盖率要排除四种无字幕区间

`title_card` / `intro` / `outro` / **段尾静音尾巴**（段落视频比旁白长）。

只排除 title_card 会误判成「成片没字幕」。

### D6. ⚠ 字幕必须在拼片阶段烧，不能成片后再烧

二次编码会把顶栏小字压糊，连累验收读不出数字。

### D7. ⚠ 字体路径写死会跨机器失效

→ 脚本里用候选列表 + 存在性检查（已内置），文档里用占位路径。

---

## F 组 —— 画面绘制（PIL）

### F1. 🚨🚨🚨 豆腐块（Menlo 无汉字）—— 用 AST 扫描，别用正则
**症状**：中文渲染成方块（.notdef），画面里出现 `□□□`。
**根因**：`F(MONO, 30)` 渲染中文 → Menlo 是**纯英文等宽字体**，无汉字字形。
**解法**：① 含中文一律用 `STHeiti`(index=1) 或 `FM()` 自动选；② 扫描用 **AST 解析 `d.text()` 调用**，按每个调用**实际传入的字体**查 cmap。

**🚨 正则扫描有三个致命盲区（实测全踩）**：
- 字符串跨行 → `[^)]*?` 提前截断 → **漏报**
- 字面量含拼接/变量 → 完全匹配不到 → **漏报**
- 扫"源码里所有中文字符串" → 把注释/docstring/`FM()` 的正常中文全算误报（实测 **2206 处假阳性**）

**验证方法（必做）**：故意注入 3 处不同形态的豆腐块（含跨行 `multiline_text`），确认扫描器**全部抓到**，再删掉。只看"扫描器说干净"不算验证过。

### F2. 🚨 PIL paste 不接受浮点坐标
**症状**：`TypeError: 'float' object cannot be interpreted as an integer`。
**根因**：`seg()`/`lerp()` 返回 float，混进 `im.paste(sprite, (x, y), m)`。
**解法**：动画淡入函数入口一律 `x, y = int(x), int(y)`。

### F3. 🚨 图形元素越界（固定偏移）
**症状**：线/标签伸出面板右边界，一直画到画布边缘。
**根因**：用了固定偏移 `hx + 118`，没考虑剩余空间。
**解法**：按 `avail = cx1 - hx` 决定左右展开方向，**所有坐标 clamp 到绘图区内**。

**⚠ 验收要点**：vision 抽查必须**专门问**"有没有越出圆角面板" —— 缩略图上肉眼容易漏。实测红线曾伸到画布边缘。

### F4. 🚨 标签互相遮挡
**症状**：两个标签框叠在一起，红框被蓝框完全压住（画面上"少了一个标签"）。
**根因**：共用同一 x/y 锚点，当两个值接近时 y 差很小。
**解法**：y 方向**强制错开**（一上一下）。

### F5. ⚠ 标注标签要放在"空白带"，不能挤在数据密集区
**症状**："价差"标签画在两条线之间 → 被"次日开盘"标签框盖住，t=0.95 也看不见。
**解法**：标签移到图表**左下角空白区**，用**引线**连回数据点。引线式比挤在数据区可靠。

### F6. 🚨🚨 动态片段帧数必须按旁白算，不能拍脑袋
**症状**：`❌ 动态段0 帧数不符: 期望 720, 实得 560` → 白跑一轮渲染。
**根因**：动态片段按"估的帧数"出片，但 build 要求**精确等于** `旁白帧数 + 0.9s 尾巴`。
**解法**：**在 build 脚本里加预检** —— 渲染任何东西之前先算需求帧数，不符就报错并**直接给出重出片命令**：
```
python frames_xxx_dyn.py --kind intro --frames {需要帧数} --out {路径}
```
帧数公式：`int(round(旁白秒数 * fps)) + int(round(0.9 * fps))`

## G 组 —— 脚本工程

### G1. 🚨 HERMES_HOME 不能重复拼 `.hermes`
**症状**：`ModuleNotFoundError: No module named 'subtitle'`。
**根因**：`HERMES_HOME` **已经是** .hermes 根目录，再拼一层 → `.hermes/.hermes/skills/...`。  # leak-ok
**解法**：`HERMES_HOME` 存在时直接用它当根，否则用家目录下的 `.hermes`；拼完 `if not SK.exists(): sys.exit(带修法的提示)`。

### G2. 🚨 openai-whisper 本机没装
**症状**：`ModuleNotFoundError: No module named 'whisper'`。
**根因**：脚本里顺手 `import whisper as _w`（真正要的是 faster_whisper）。
**解法**：只 `from faster_whisper import WhisperModel`，别加多余的 openai-whisper import。

### G3. 🚨 生产脚本里的绝对路径 → 脱敏扫描器 BLOCK
**症状**：`❌ [BLOCK] 本机路径 build_ep2.py:29`。
**根因**：脚本硬编码了 skill 路径。
**解法**：走环境变量 + fallback（`HERMES_HOME` 当根，或家目录下的 `.hermes`），再用 `TUTORIAL_SKILL_SCRIPTS` 覆盖。
**⚠ 顺带好处**：换机也能跑。

### G4. 🚨 扫描器分不清"生产脚本"和"发布内容"
**症状**：整目录扫描，生产脚本里的路径/注释被判 BLOCK，但这些**根本不会出现在视频里**。
**解法**：**两个都做** —— ① 生产脚本改成路径参数化（G3）；② 发布前**只扫交付物**（成片抽帧 + 发布文案 + 字幕原文），别扫整个工作目录。

### G5. ⚠ 固定值要写进 skill，不要每期重算
**症状**：同一批检查每期都要重写一遍脚本。
**根因**：踩过的坑只修在当期的脚本里，下期还得再踩。
**解法**：把当期的**扫描/验收脚本直接放进 skill**，下期只改内容不改工具。

## E 组 —— 脱敏与路径

### E1. 🚨🚨🚨 扫 mp4 二进制 → 几百处假 BLOCK

**症状**：检查器报「584 处 BLOCK —— 不许发出去」，但视频是干净的。

**根因**：把压缩码流当文本扫 → 随机字节被匹配成敏感词。

**对照**：旧版**已验收的**成片同样报 585 处 → 证明是系统性误报。

**解法**：
- 文本类物料（文案/简介/标题/标签）→ 全量扫
- 视频/音频 → **抽帧 OCR + 音轨转写**

```bash
python scripts/scan_leak.py publish_copy.md
python scripts/scan_leak.py final.mp4 --ocr --transcribe
```

### E2. 🚨 文档里写绝对路径 → 文档自己被判 BLOCK

`check_leak.py` 扫文档时，会把文档里写的检查命令里的绝对路径判成 BLOCK（退出码 1），**但不在正文里**。

→ 对外文档一律写**占位命令**（`python scripts/scan_leak.py <本目录>`）。

### E3. 🚨 `~` 展开成 agent 的 home，不是真 home

实测 `~` → `<agent_home>`，`<agent_home>/.hermes/skills/...` **不存在**（真 home 下没有 `.hermes` 子目录）。  # leak-ok

→ **调用 skill 里的脚本一律用绝对路径**，别用 `~` 展开。

### E4. ⚠ 交付物 = 成片文件本身

把 mp4 路径放进回复（`MEDIA:<绝对路径>`），别只说"做完了"。

### E5. 🚨🚨 扫描器会把自己判成 BLOCK

**症状**：第一次跑自带扫描器扫自己的 skill 目录 → 报 31 处 BLOCK，全是规则表里的敏感词本身。

**根因**：扫描器的 `BLOCK_PATTERNS` 里写着"策略名|Agent名|私有路径|业绩数字"这些词，
扫自己的规则表 → **必然命中**。

**解法**：**逐行加 `leak-ok` 豁免**（扫描器读文本时跳过含该标记的行）：
```python
BLOCK_PATTERNS = [
    (r"...敏感词正则...", "策略身份"),  # leak-ok
]
```
⚠ **文档里的教学性反例也要逐行豁免**（如"要防 `~/.hermes/skills/...`"这类举例）。  # leak-ok
实测：31 处 → 3 处 → **0**。

⚠ **用一个独立的外部检查器交叉验证** —— 同实现可能有同盲区。
⚠ 顺手扫**别的** skill：本次扫 `real-app-tutorial-video` 发现 6 处原有 BLOCK
（`~/.hermes` 路径、`/Users/...` 绝对路径、Agent 名、私有资产名）→ **skill 本身也是可对外交付物**。  # leak-ok

完整自检清单见 `references/sourcing-and-selfcheck.md`。
