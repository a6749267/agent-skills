#!/usr/bin/env python3
"""分段动态配乐混音 —— 三段独立音量 + 口播主导。

用户要求:
  ① 片头 / 正文 / 片尾 **用曲子的不同段落**, 不只是音量不同
  ② 三段 **音量不同**
  ③ **口播永远最大** —— 音乐给旁白让路

🔴 踩过的坑(逐段实测出来的, 别再犯):
  ① **loudnorm 在混音末端 → 把三段相对音量压平**
     实测片头比正文只高 2.4dB、片尾反而低 3.1dB → 分段设计全废。
     修法: loudnorm **只作用于口播轨**(单独归一化), 终混不再过 loudnorm。
  ② **sidechaincompress 作用于整条音乐轨 → 分段关系被抹平**
     片头/片尾无旁白 → 闪避不触发 → makeup 把音乐抬起; 正文有旁白 → 音乐被压
     → 结果正文最轻, 与设计完全相反。
     修法: **闪避只做正文段**, 片头/片尾直通。
  ③ **时间轴错位 → 标题卡区一片死寂**
     intro 音乐段长度按"片头4.2s"取, body 段 adelay 到 body_s=4.2
     → 两段之间有空档, 标题卡(4.2~6.12s)正好落在空档里 → LUFS -42。
     修法: 按**时间轴**对齐: intro 音乐 0~6.12s / body 6.12~205.48 / outro 205.48~210.08。
  ④ **volume 表达式的 t 是流内时间轴** → 标题卡区间要用成片绝对时间时要减偏移, 极易错。
     修法: 用**独立 volume 滤镜 + enable 区间**(enable 用的是输出时间轴=绝对时间)。
  ⑤ **滤镜标签不能复用** → 同一标签既进 amix 又要导出 → 必须 asplit,
     但主渲染里 asplit 的另一支未连接 → ffmpeg 报 exit 234。
     修法: **主图和 stem 图分开构建**, 不要在同一张图里分叉。

时间轴(timeline.json):
  intro       0.00 ~   4.20   (4.20s)
  title_card  4.20 ~   6.12   (1.92s)   ← 8 张标题卡, 各自独立
  segment×8   6.12 ~ 205.48
  outro     205.48 ~ 210.08   (4.60s)

曲子三段取材(623 的 1s 能量曲线实测):
  intro ← 165.0s  (onset峰值245, 最高潮)      → 配片头大字, 冲击最强
  body  ← 14.0s   (连续驱动, 含自然 break)   → 垫底, 不抢口播
  outro ← 231.0s  (强收束句)                 → 收尾有存在感
"""
import json
import pathlib
import subprocess
import sys

FF = "ffmpeg"

bgm = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/vid5/music/bgm_new.mp3")
out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/vid5/out/final_v8.mp4")
silent = pathlib.Path(sys.argv[3] if len(sys.argv) > 3 else "/tmp/vid5/out/silent.mp4")

TL = json.loads(pathlib.Path("/tmp/vid5/out/timeline.json").read_text())
total = TL["total"]
tracks = TL["tracks"]

intro_s = tracks[0]["start"]
first_seg_s = next(t["start"] for t in tracks if t["kind"] == "segment")
outro_s = next(t["start"] for t in tracks if t["kind"] == "outro")
outro_e = next(t["end"] for t in tracks if t["kind"] == "outro")
cards = [(t["start"], t["end"]) for t in tracks
         if t["kind"] == "title_card" and t["start"] >= first_seg_s]

print(f"成片 {total:.2f}s | 正文起点 {first_seg_s:.2f}s | 片尾 {outro_s:.2f}~{outro_e:.2f}s")
print(f"中间标题卡 {len(cards)} 张")

# ── 增益设计(dB, 相对曲子原始电平) ──
GAIN = {"intro": -4.0, "body": -20.0, "outro": -8.0}
CARD_GAIN = -11.0      # 中间 7 张标题卡(叠在 body 上)
VOICE_LN = "loudnorm=I=-18:TP=-3:LRA=11"
BG_WEIGHT = 0.34

SRC = {"intro": 165.0, "body": 14.0, "outro": 231.0}
need = {"intro": first_seg_s - intro_s,
        "body": outro_s - first_seg_s,
        "outro": outro_e - outro_s}
order = ["intro", "body", "outro"]

inputs = []
for name in order:
    take = need[name] + (1.0 if name == "intro" else 1.6)
    inputs += ["-ss", f"{SRC[name]:.3f}", "-t", f"{take:.3f}", "-i", str(bgm)]


def build_music_chain(sink_voice=False):
    """音乐侧滤镜链 → 输出标签 [bgmix]。主图和 stem 图共用。

    sink_voice=True 时把口播分支接一个 anull 泄流 ——
    stem 图里不用口播, 但 [va] 标签若悬空 ffmpeg 会报 exit 234
    ("Output label 'va' does not exist" / 未连接输出)。
    """
    fc = []
    labels = {}
    for i, name in enumerate(order):
        idx = i + 1
        g, nd = GAIN[name], need[name]
        delay_ms = 0
        if name == "body":
            delay_ms = int(round(first_seg_s * 1000))
        elif name == "outro":
            delay_ms = int(round(outro_s * 1000))
        fi = 0.25 if name == "intro" else (0.5 if name == "outro" else 0.8)
        fo = 0.0 if name == "intro" else (1.6 if name == "outro" else 1.5)
        chain = (f"[{idx}:a]atrim=start=0:end={nd:.3f},"
                 f"asetpts=N/SR/TB,"
                 f"volume={g}dB,"
                 f"highpass=f=45,"
                 f"afade=t=in:st=0:d={fi:.2f}")
        if fo > 0:
            chain += f",afade=t=out:st={max(0.0, nd - fo):.3f}:d={fo:.2f}"
        if delay_ms:
            chain += f",adelay={delay_ms}|{delay_ms}"
        chain += f",apad=whole_dur={total:.3f}[bgraw_{name}]"
        fc.append(chain)
        labels[name] = f"bgraw_{name}"

    # 中间标题卡抬音乐: 独立 volume 滤镜 + enable 区间(绝对时间)
    delta = CARD_GAIN - GAIN["body"]
    cur = labels["body"]
    for k, (a, b) in enumerate(cards):
        nxt = f"bg_card{k}"
        fc.append(f"[{cur}]volume={delta}dB:enable='between(t,{a:.3f},{b:.3f})'[{nxt}]")
        cur = nxt
    labels["body"] = cur

    # 口播: 单独 loudnorm → 主导, 分两路(直通 + 闪避触发)
    fc.append(f"[0:a]{VOICE_LN},highpass=f=90,asplit=2[va][sc]")
    # 闪避只做正文段(片头/片尾直通)
    fc.append(f"[{labels['body']}][sc]sidechaincompress=threshold=0.02:ratio=10:"
              f"attack=10:release=380:makeup=1[bg_body_duck]")
    # 三段音乐合并
    fc.append(f"[{labels['intro']}][bg_body_duck][{labels['outro']}]"
              f"amix=inputs=3:duration=longest:dropout_transition=0:normalize=0[bgmix]")
    return fc


# ── 主渲染: 音乐 + 口播 → 成片 ──
fc = build_music_chain()
fc.append(f"[va][bgmix]amix=inputs=2:duration=first:dropout_transition=0:"
          f"weights=1 {BG_WEIGHT}:normalize=0,"
          f"alimiter=limit=0.94,"
          f"aresample=44100[a]")

cmd = [FF, "-y", "-loglevel", "error", "-i", str(silent)] + inputs + [
    "-filter_complex", ";".join(fc),
    "-map", "0:v", "-map", "[a]",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
    "-movflags", "+faststart", str(out)]
print("\n".join(cmd))
subprocess.run(cmd, check=True)
print(f"\n✅ {out} {out.stat().st_size // 1024 // 1024}MB")

# ── 音乐贡献实测: **同一张图**, 口播权重置 0 → 得到"成片里音乐实际贡献"的电平
#   (比单独导 stem 更诚实: 包含了 amix 权重与 limiter 的影响)
stem = out.with_name(out.stem + "_musiconly.wav")
fc_stem = build_music_chain()
fc_stem.append(f"[va][bgmix]amix=inputs=2:duration=first:dropout_transition=0:"
               f"weights=0 {BG_WEIGHT}:normalize=0,"
               f"alimiter=limit=0.94,"
               f"aresample=44100[a]")
cmd2 = [FF, "-y", "-loglevel", "error", "-i", str(silent)] + inputs + [
    "-filter_complex", ";".join(fc_stem),
    "-map", "[a]", "-c:a", "pcm_s16le", str(stem)]
subprocess.run(cmd2, check=True)
print(f"🎵 纯音乐轨 {stem} {stem.stat().st_size // 1024 // 1024}KB")
