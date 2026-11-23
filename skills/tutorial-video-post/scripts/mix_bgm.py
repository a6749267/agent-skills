#!/usr/bin/env python3
"""分段动态配乐混音 —— 片头/正文/片尾 各自取材 + 各自音量 + 词间回弹 + 冲击包络。

时间轴(timeline.json):
    {"total": 253.32,
     "tracks": [{"kind":"intro","start":0.0,"end":24.36},
                {"kind":"segment","start":24.36,"end":54.92}, ...,
                {"kind":"outro","start":230.72,"end":253.32}]}

🎯 激昂感来自三件事(不是加音量):
  ① 段落整体抬升          → GAIN
  ② **瞬态冲击**: 段首直接满能量切入(从重拍头部进入), 不做静默蓄势
  ③ **词间回弹**: 闪避 release 缩短(380ms → 160ms)
     口播一停, 音乐立刻顶回来 → 词与词之间有"呼吸感"
     (release=380ms 时音乐一直被压着, 听起来"闷", 不激昂)

🔴 踩过的坑(逐条实测出来的, 别再犯):
  ① **loudnorm 放在混音末端 → 三段相对关系被压平**
     修法: loudnorm **只作用于口播轨**, 终混只留 alimiter。
  ② **闪避没做 → 音乐盖死旁白**(AST Speech 掉到 0.18)
     修法: 闪避必须有。⚠ 但**有旁白的段都要闪避**, 别信"片头无旁白直通"的旧说法。
  ③ **闪避会把片头音乐压死** —— 旁白从 0.1s 就开始说话时必踩
     实测: 纯音乐轨前 1.5s 全是 −44~−50dB, 音乐像"断了"(源本身 −11.8dBFS 满能量)。
     修法: **只门控触发信号 [sc]**, 让片头前 gate 秒旁白不触发闪避。
     ⚠ 只能门控 [sc](asplit 的触发副本), 绝不能动 [va](真正的声音) → 否则旁白被静音。
  ④ **豁免期音乐全程满能量 → 旁白被糊**
     实测: 旁白/音乐比只有 +23dB。
     修法: **冲击 + 快速回落** → t=0 满能量, fall 秒内落到垫底电平, 再恒定。
     实测: 旁白/音乐比 +23.4dB → **+32.5dB**。
  ⑤ **两个 volume=enable= 串联 → enable 关闭时整个滤镜被跳过 → 音量变 0dB(全段最大)**
     修法: 用**一条 volume + eval=frame 表达式**算完整包络。
  ⑥ **满电平硬切 → 第 0 帧 digital click**
     修法: 段首加 ramp(默认 30ms, 不到 1 帧, 人耳听不出淡入, 只消爆音)。
  ⑦ **串联两个 atrim → 配乐末尾凭空消失**
     修法: **单个 atrim** + `-stream_loop -1`。

用法:
    python mix_bgm.py --bgm bgm.mp3 --silent silent.mp4 --timeline timeline.json --out final.mp4
    python mix_bgm.py ... --config mix_config.json
"""
import argparse
import json
import pathlib
import subprocess
import sys


def build_filter(bgm, silent, timeline, cfg):
    TL = json.loads(pathlib.Path(timeline).read_text())
    total = TL["total"]
    tracks = TL["tracks"]
    order = [t["kind"] for t in tracks if t["kind"] in ("intro", "segment", "outro")]
    # 段落起点(绝对时间)
    bounds = {}
    for t in tracks:
        if t["kind"] in ("intro", "segment", "outro"):
            bounds.setdefault(t["kind"], []).append(t)
    seg_start = {}
    seg_end = {}
    for kind, lst in bounds.items():
        seg_start[kind] = min(t["start"] for t in lst)
        seg_end[kind] = max(t["end"] for t in lst)
    intro_s = seg_start.get("intro", 0.0)
    intro_e = seg_end.get("intro", 0.0)
    body_s = seg_start.get("segment", intro_e)
    body_e = seg_end.get("segment", body_s)
    outro_s = seg_start.get("outro", body_e)
    outro_e = seg_end.get("outro", outro_s)

    kinds = ["intro", "body", "outro"]
    need = {"intro": intro_e - intro_s, "body": body_e - body_s, "outro": outro_e - outro_s}

    GAIN = cfg.get("gain", {"intro": -8.5, "body": -20.0, "outro": -8.5})
    DUCK = cfg.get("duck", {
        "body": dict(threshold=0.015, ratio=12, attack=4, release=200),
        "intro": dict(threshold=0.02, ratio=14, attack=3, release=160),
        "outro": dict(threshold=0.02, ratio=14, attack=3, release=160),
    })
    # 闪避豁免: 段首 N 秒不闪避(让冲击完整落地)
    GATE = cfg.get("duck_gate", {"intro": 0.8, "body": None, "outro": None})
    GATE_RAMP = cfg.get("duck_gate_ramp", 0.15)
    # 豁免期音乐的衰减: 冲击 → 快速回落 → 垫底
    FALL = cfg.get("gate_fall", {"intro": 0.30, "body": None, "outro": None})
    FALL_DB = cfg.get("gate_fall_db", -13.0)
    # 段首包络: sil(蓄势) / boom(冲击陡升) / ramp(防 click 最小斜坡)
    IMPACT = cfg.get("impact", {
        "intro": dict(sil=0.0, boom=0.0, ramp=0.03),
        "body": dict(sil=0.0, boom=0.0, ramp=0.8),
        "outro": dict(sil=0.6, boom=0.30, ramp=0.5),
    })
    SRC = cfg.get("src", {"intro": 0.0, "body": 0.0, "outro": 0.0})
    VOICE_LN = cfg.get("voice_loudnorm", "loudnorm=I=-18:TP=-3:LRA=11")
    BG_WEIGHT = cfg.get("bg_weight", 0.34)
    OUT_FADE = cfg.get("out_fade", {"intro": 0.35, "body": 1.5, "outro": 1.6})
    OUTRO_LIFT = cfg.get("outro_lift", True)

    print(f"成片 {total:.2f}s | intro {intro_s:.2f}~{intro_e:.2f} | "
          f"body {body_s:.2f}~{body_e:.2f} | outro {outro_s:.2f}~{outro_e:.2f}")

    # ── 输入: 每段一个音频输入 ──
    inputs = []
    for name in kinds:
        take = need[name] + (1.0 if name == "intro" else 1.6)
        inputs += ["-ss", f"{SRC[name]:.3f}", "-t", f"{take:.3f}", "-i", str(bgm)]

    def env_expr(sil, boom, ramp):
        if sil > 0 and boom > 0:
            return (f"if(lt(t,{sil:.3f}),0.000178,"
                    f"if(lt(t,{sil + boom:.3f}),(t-{sil:.3f})/{boom:.3f},1))")
        if sil > 0:
            return f"if(lt(t,{sil:.3f}),0.000178,1)"
        if ramp > 0:
            return f"if(lt(t,{ramp:.4f}),(t/{ramp:.4f}),1)"
        return "1"

    def build_chain():
        fc = []
        labels = {}
        for i, name in enumerate(kinds):
            idx = i + 1
            g, nd = GAIN[name], need[name]
            delay_ms = 0
            if name == "body":
                delay_ms = int(round(body_s * 1000))
            elif name == "outro":
                delay_ms = int(round(outro_s * 1000))
            imp = IMPACT[name]
            env = env_expr(imp["sil"], imp["boom"], imp.get("ramp", 0.0))
            chain = (f"[{idx}:a]atrim=start=0:end={nd:.3f},"
                     f"asetpts=N/SR/TB,"
                     f"volume={g}dB,"
                     f"highpass=f=45,"
                     f"volume='{env}':eval=frame")
            fo = OUT_FADE.get(name, 0.0)
            if fo > 0:
                if name == "outro" and OUTRO_LIFT:
                    # 收束前先冲高再落 → 收得有张力而不是泄气
                    lift_st = max(0.0, nd - fo - 1.2)
                    chain += (f",volume='if(lt(t,{lift_st:.3f}),1,"
                              f"if(lt(t,{nd - fo:.3f}),1.19,0.84))':eval=frame")
                chain += f",afade=t=out:st={max(0.0, nd - fo):.3f}:d={fo:.2f}"
            else:
                chain += f",afade=t=out:st={max(0.0, nd - 0.35):.3f}:d=0.35"
            if delay_ms:
                chain += f",adelay={delay_ms}|{delay_ms}"
            chain += f",apad=whole_dur={total:.3f}[bgraw_{name}]"
            fc.append(chain)
            labels[name] = f"bgraw_{name}"

        # 口播: 单独 loudnorm → 主导, 分两路(直通 + 闪避触发)
        fc.append(f"[0:a]{VOICE_LN},highpass=f=90,asplit=2[va][sc]")
        for name in kinds:
            dk = DUCK[name]
            gate = GATE.get(name)
            if gate:
                r = GATE_RAMP
                fc.append(f"[sc]volume='if(lt(t,{gate:.3f}),0,"
                          f"if(lt(t,{gate + r:.3f}),(t-{gate:.3f})/{r:.3f},1))'"
                          f":eval=frame[scg_{name}]")
                sc = f"[scg_{name}]"
                fall = FALL.get(name)
                if fall:
                    dbv = 10 ** (FALL_DB / 20.0)
                    env2 = (f"if(lt(t,{fall:.3f}),1+({dbv:.6f}-1)*(t/{fall:.3f}),"
                            f"if(lt(t,{gate:.3f}),{dbv:.6f},"
                            f"if(lt(t,{gate + r:.3f}),"
                            f"{dbv:.6f}+(1-{dbv:.6f})*(t-{gate:.3f})/{r:.3f},1)))")
                    fc.append(f"[{labels[name]}]volume='{env2}':eval=frame[bgf_{name}]")
                    labels[name] = f"bgf_{name}"
            else:
                sc = "[sc]"
            fc.append(f"[{labels[name]}]{sc}sidechaincompress="
                      f"threshold={dk['threshold']}:ratio={dk['ratio']}:"
                      f"attack={dk['attack']}:release={dk['release']}:makeup=1"
                      f"[bg_{name}_duck]")
        fc.append(f"[bg_intro_duck][bg_body_duck][bg_outro_duck]"
                  f"amix=inputs=3:duration=longest:dropout_transition=0:normalize=0[bgmix]")
        return fc, labels

    fc, _ = build_chain()
    fc.append(f"[va][bgmix]amix=inputs=2:duration=first:dropout_transition=0:"
              f"weights=1 {BG_WEIGHT}:normalize=0,"
              f"alimiter=limit=0.94,"
              f"aresample=44100[a]")

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent)] + inputs + [
        "-filter_complex", ";".join(fc),
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart", str(cfg.get("_out"))]

    # 纯音乐轨(口播权重 0)→ 用来量各段音乐实际贡献 / 验包络
    fc2, _ = build_chain()
    fc2.append(f"[va][bgmix]amix=inputs=2:duration=first:dropout_transition=0:"
               f"weights=0 {BG_WEIGHT}:normalize=0,"
               f"alimiter=limit=0.94,"
               f"aresample=44100[a]")
    stem = str(cfg.get("_out")).replace(".mp4", "_musiconly.wav")
    cmd2 = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent)] + inputs + [
        "-filter_complex", ";".join(fc2),
        "-map", "[a]", "-c:a", "pcm_s16le", stem]
    return cmd, cmd2, stem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bgm", required=True)
    ap.add_argument("--silent", required=True, help="无声底版(画面+旁白)")
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default="", help="JSON 配置(覆盖默认参数)")
    args = ap.parse_args()

    cfg = {}
    if args.config:
        cfg = json.loads(pathlib.Path(args.config).read_text())
    cfg["_out"] = args.out

    cmd, cmd2, stem = build_filter(args.bgm, args.silent, args.timeline, cfg)
    subprocess.run(cmd, check=True)
    out = pathlib.Path(args.out)
    print(f"✅ {out} {out.stat().st_size // 1024 // 1024}MB")
    subprocess.run(cmd2, check=True)
    print(f"🎵 纯音乐轨 {stem}")


if __name__ == "__main__":
    main()
