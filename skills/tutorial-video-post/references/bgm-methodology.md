# 配乐方法论 —— 从"随便配个BGM"到"能交付"

> 这条链路的顺序不能乱：**下载 → 识别 → 选段 → 混音 → 验收**。
> 跳过"识别"直接凭感觉选曲，是 90% 翻车的来源。

---

## 一、下载：候选池决定成败

### 为什么 genre 是第一道关

用户听完骂"垃圾配乐"的时候，90% 不是混音的问题，是**候选池里根本没有带劲的东西**。

只抓这些 genre → 选出来的必然是 corporate 励志：
`epic / cinematic / technology / upbeat / trailer / dramatic`

必须补进这些（动感池）：
`techno / house / drum-and-bass / electronic / trance / edm / dubstep / breakbeat / funk / rock / hip-hop / bass / groove / synthwave`

明确排除：
`cinematic / ambient / chillout / corporate / sad / calm / emotional / motivational`

```bash
python scripts/fetch_music.py --out music_pool \
    --genres techno,house,edm,drum-and-basses,electronic,trance,dubstep,breakbeat,funk,rock \
    --per-genre 20 --sleep 1.2
```

### 抓取纪律

- 每 genre 间隔 ≥1s（脚本默认 `sleep=1.2`）
- 429 要退避（脚本内置 ×6 退避）
- 落地文件 <20KB 的多半是错误页 → 直接跳过
- `manifest.json` 记录每 genre 抓到几首 → 便于回溯

---

## 二、识别：三个独立证据源

### 为什么不能用频谱图

高端 Corporate 配乐和真带劲的电子乐，频谱**几乎一样**：
都是"高频延展 + 低频厚 + 有起伏"。视觉模型给两者都高分。

**频谱图只能回答"有没有能量"，回答不了"是什么音乐"。**

### ① AST（AudioSet 527 类）

| 标签 | 含义 |
|---|---|
| `Techno` `House music` `Trance music` `Drum and bass` `Electronic music` | 带劲 |
| `Bass drum` `Drum machine` `Drum roll` `Snare drum` `Beat` | 有推动力 |
| `Violin` `Cello` `Sad music` `Ambient music` `Choir` `Singing bowl` | **排除** |

⚠ **AST 跑在纯音乐片段上时 `Speech=0.000` 是正常的** —— 候选里本来没人声。
这不是"AST 判不了"，而是它正常工作。它在这里的职责只有一个：**测这段音乐有多猛**。

### ② CLAP（零样本音频-文本）

直接问模型"像不像带劲的科技配乐"。

```
margin = mean(正向提示相似度) − mean(负向提示相似度)
```

**margin ≤ 0 = 比 corporate 垃圾还像垃圾。**

正向提示：`energetic electronic music with driving beat and build up` /
`powerful epic percussion hit with impact` /
`upbeat techno with strong bass drop` /
`dynamic soundtrack with sharp transients`

负向提示：`boring generic corporate stock loop` /
`soft calm ambient pad with no rhythm` /
`sad slow cinematic violin melody` /
`flat background music without dynamics`

⚠ transformers 版本差异：`get_text_features` 可能返回 ModelOutput → 取 `pooler_output`；
`proc(audios=)` 已改名 `proc(audio=)`。

### ③ 可听要素（区分"墙纸"与"驱动"）

| 指标 | 含义 | 墙纸 | 带劲 |
|---|---|---|---|
| 鼓点密度（onset/s） | 有没有推动力 | < 0.8 | 高 |
| 瞬态锐度 | 鼓点是"啪"地砸下还是软的 | 低 | 高 |
| 低频冲击（30–140Hz 占比） | 有没有落地感 | 低 | 高 |
| 动态范围（crest） | 有没有强弱对比 | 平坦 | 大 |

🚨 **STFT 窗长必须 >> hop**（这是低频分析的命门）：
用 `hop=10ms` 同时当 FFT 长度 → 分辨率 100Hz/bin → 20–200Hz 只有 1 个 bin → kick 能量全为 0。
正确：**窗长 2048~4096，hop 512**。

---

## 三、选段：三维交叉验证

### 为什么必须交叉

三个维度会**互相矛盾** —— 实测某曲子的 outro 候选，AST Music 分最高（0.794）但冲击标签只命中 2/4。
**任何单一维度都会选错。**

| 维度 | 指标 | 判据 | 为什么 |
|---|---|---|---|
| 人声带占用 | 300–3400Hz **功率密度** dB | 越**低**越好 | 决定会不会压旁白 |
| 低频冲击 | 55–120Hz 功率密度 dB | 越高越好 | 决定有没有"砰" |
| 激昂度 | AST 冲击标签命中 + Music 分 | 4/4 | 决定曲子本身带不带劲 |

### 🚨 功率密度，不是能量占比

宽带音乐的幅度谱几乎是平的 → 占比 ≈ 带宽/奈奎quist = 38.75%，**跟内容无关**。

必须算**功率密度**并**等带宽归一**：
```python
avg_density = P.sum() / (freqs[-1] - freqs[0])   # 每 Hz 平均功率
band_density = P[(freqs>=lo)&(freqs<hi)].sum() / (hi-lo)
db = 10*np.log10(band_density / avg_density)
```
判据：< 0dB = 该带比平均安静（可当底）；0~3dB = 偏亮；>3dB = 突出（压旁白）。

实测同一首曲子：
```
intro_26    人声带 -5.22dB  冲击 +17.14dB   ← intro 最优
outro_196   人声带 -3.59dB  冲击 +17.13dB   ← outro 最优
outro_231   人声带 -0.14dB  冲击 +14.89dB   ← 旧选，最脏
```

### 🚨 片头切入点要预滚到重拍前 0.25s

**根因**：鼓组的 kick 是**在落点才砸下的**，落点前 200ms 是起音期。
切在落点 → 开头是"浮起来"，不是高潮。

**做法**：**切入点 = 落点 − 0.25s**。
扫描所有重拍 × 多种预滚 → 0.25s 是共同最优（实测 13 个落点里 10 个命中）。

实测：切落点首 100ms = −11.8dBFS（爬升） vs 切落点−0.25s = **−8.23dBFS**（满能量）。

onset 检测用**谱通量**（spectral flux），别用 RMS 差分。

### 三段取材点

| 段 | 目标 | 判据 |
|---|---|---|
| intro | 冲击最强 + 人声带最低 + 满能量切入 | 冲击标签 4/4，人声带最低 |
| body | 连续驱动、平稳 | 能量平稳偏高的连续区间 |
| outro | 有收束感 + 不压旁白 | 冲击够 + 人声带低 |

**三段必须互不重叠**，否则 intro 和 outro 听到同一段旋律。

---

## 四、混音：五层设计

```
口播轨 ──loudnorm──┬──────────────────────────→ [va] ─┐
                   └→ [sc] (触发副本)                    │
                     ↑                                     │
音乐 ─ atrim→volume→highpass→volume(env)→afade→apad     │
                     ↓                                     │
              [sc] 门控(片头豁免)                          │
                     ↓                                     │
              sidechaincompress ←──────────────────────────┤
                     ↓                                     │
              volume(冲击衰减包络)                          │
                     ↓                                     │
                   [bgmix] ←───────────────────────────────┘
                     ↓
              amix(voice:1, bg:0.34) → alimiter → [a]
```

### 层 1：口播主导

`loudnorm=I=-18:TP=-3:LRA=11` **只做在口播轨上**。
终混末端只留 `alimiter=limit=0.94` —— loudnorm 放末端会把三段相对关系压平。

### 层 2：段落增益

```
intro : -8.5dB
body  : -20.0dB
outro : -8.5dB
```
→ intro/outro 比 body 高 **~11.8dB**（实测），层次明显但不盖旁白。

### 层 3：段首包络

```
intro : sil=0.0  boom=0.0   ramp=0.03   ← 直接满能量切入
body  : sil=0.0  boom=0.0   ramp=0.8
outro: sil=0.6  boom=0.30  ramp=0.5    ← 片尾是收束, 保留蓄势
```
🚨 **ramp 不是淡入**，是**防 click 的最小斜坡**。30ms = 0.75 帧，人耳听不出。
满电平硬切会有 digital click（实测前 8 样本全 0 的干净边界 vs 有跳变的爆音）。

### 层 4：闪避 + 豁免 + 衰减回落

这是"激昂"的**核心三层**：

| 机制 | 参数 | 作用 |
|---|---|---|
| 闪避 | threshold 0.02 / ratio 14 / attack 3ms | 说话瞬间音乐几乎消失 |
| **回弹** | release **160ms** | 口播一停，音乐立刻顶回来 → 词间有呼吸感 |
| **豁免** | 片头前 0.8s 门控 `[sc]` | 让冲击完整落地（否则被压死） |
| **回落** | 0.3s 内降到 −13dB | 冲击之后音乐沉下去，旁白浮出来 |

**豁免只能门控 `[sc]`，绝不能动 `[va]`**（否则旁白被静音）。

**回落包络**：
```python
env = (f"if(lt(t,{fall}),1+({dbv}-1)*(t/{fall}),"
       f"if(lt(t,{gate}),{dbv},"
       f"if(lt(t,{gate+r}),{dbv}+(1-{dbv})*(t-{gate})/{r},1)))")
```

实测效果：
| 方案 | 旁白/音乐比 |
|---|---|
| 豁免无回落 | +23.4dB |
| **豁免 + 回落** | **+32.5dB** |

### 层 5：片尾收束

淡出前 1.2s 先抬 2.5dB 再收 → 收得有张力而不是泄气。

---

## 五、验收：四道必跑

| # | 检查 | 方法 | 判据 |
|---|---|---|---|
| 1 | 旁白清晰度 | whisper 转写成片 vs 干声，**拼音序列**比 | ratio ≥ 0.85 |
| 2 | 旁白/音乐比 | musiconly 反推音乐贡献后算残差 | ≥ +20dB |
| 3 | 冲击包络 | musiconly 首 50ms vs 后续 + click 检测 | 满能量切入 / 0 click |
| 4 | 帧完整性 | ffprobe 所有 pts 间隔 | 恒为 1/fps |

```bash
python scripts/verify_audio.py final.mp4 --silent silent.mp4 --timeline timeline.json
```

### 三个容易误判的点

1. **SNR 不扣音乐贡献** → 算出负值假警报
2. **whisper 用繁简映射表** → 漏一个就崩
3. **whisper 窗口太短（3s+beam1）** → 转写抖动被当成音频问题

---

## 六、一句话总结

> **激昂不是加音量。**
> 是「重拍处切入的满能量冲击」+「词间 160ms 回弹」+「冲击后 0.3s 快速回落让旁白出来」。
> 任何一环缺失，听感就是"闷"或"糊"。
