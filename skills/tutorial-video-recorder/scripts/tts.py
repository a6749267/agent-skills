#!/usr/bin/env python3
"""旁白 TTS —— MiniMax t2a_v2

用法:
    python tts.py --script script.json --out-dir audio

script.json 格式(每项一段):
    [{"title": "开场", "text": "旁白内容……"}, ...]
输出:
    audio/seg00.mp3, audio/seg01.mp3, ...

🚨 两个必须知道的坑:
  1. 返回的 data.audio 是 **hex 字符串**, 不是 base64。
     用 base64.b64decode() 解会得到一个「长得像 mp3 但 ffprobe 报 Invalid data」的坏文件,
     而且**不报错** —— 后面合成时才炸, 极难定位。
     正确: binascii.unhexlify()
  2. .env 里读 key 不要用正则 `re.search(r'^KEY=(.*)$')` ——
     这类形态会被某些工具的密钥脱敏规则误判, 把源码里的正则打码成 `=***` → SyntaxError。
     正确: 逐行 split('=', 1) 解析。
"""
import argparse
import base64
import binascii
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agentenv  # noqa: E402

MINIMAX_URL = "https://api.minimaxi.com/v1/t2a_v2"


def find_env_file():
    """定位凭据文件。

    🚨 坑③: **不能用 Path.home()** —— agent 沙箱常把 HOME 劫持成自己的 home,
    真正的凭据文件在别处(如 $AGENT_HOME/.env)。用 Path.home() 会得到
    <sandbox_home>/.env → 不存在 → 静默拿不到 key。
    完整的优先级链见 agentenv.py —— 这里是薄封装, 便于本脚本独立运行。
    """
    return agentenv.find_env_file()


def read_env(path=None, name="MINIMAX_CN_API_KEY"):
    """从凭据文件取值。逐行 split, 不用正则 —— 见模块 docstring 坑②。"""
    if path:
        return agentenv.read_env_value(name, path)
    return agentenv.read_env_value(name)


def synth_one(text, key, model="speech-02-hd", voice_id="Chinese (Mandarin)_Warm_Girl",
              speed=1.0, sample_rate=32000, bitrate=32000):
    """返回 mp3 bytes。失败抛异常, 不要静默返回空。"""
    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1, "pitch": 0},
        "audio_setting": {"sample_rate": sample_rate, "bitrate": bitrate, "format": "mp3"},
    }
    req = urllib.request.Request(
        MINIMAX_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        raw = urllib.request.urlopen(req, timeout=120).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"MiniMax HTTP {e.code}: {e.read()[:300].decode('utf-8','replace')}") from e
    j = json.loads(raw)
    br = j.get("base_resp") or {}
    if br.get("status_code") not in (0, None):
        raise RuntimeError(f"MiniMax 业务错误: {br}")
    au = (j.get("data") or {}).get("audio") or ""
    if not au:
        raise RuntimeError(f"MiniMax 没返回音频: {str(j)[:200]}")
    # 坑①: hex, 不是 base64
    if re.fullmatch(r"[0-9a-fA-F]+", au[:200]):
        return binascii.unhexlify(au)
    return base64.b64decode(au)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, help="script.json 路径")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default="speech-02-hd")
    ap.add_argument("--voice-id", default="Chinese (Mandarin)_Warm_Girl")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--force", action="store_true", help="已存在也重生成")
    a = ap.parse_args()

    key = read_env()
    if not key:
        sys.exit("❌ 找不到 MINIMAX_CN_API_KEY —— 设 env 或写进 $AGENT_HOME/.env")

    items = json.loads(pathlib.Path(a.script).read_text())
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for i, it in enumerate(items):
        dst = out / f"seg{i:02d}.mp3"
        if dst.exists() and not a.force:
            print(f"[{i}] {dst.name} 已存在, 跳过")
            continue
        mp3 = synth_one(it["text"], key, a.model, a.voice_id, a.speed)
        if not mp3.startswith(b"ID3") and not mp3.startswith(b"\xff"):
            print(f"  ⚠️ {dst.name} 不像 mp3 (head={mp3[:8]!r}) —— 可能是 hex 解码方式不对")
        dst.write_bytes(mp3)
        print(f"[{i}] {dst.name} {len(mp3)//1024}KB  「{it.get('title','')}」")

    print(f"\n✅ {len(items)} 段旁白 → {out}")


if __name__ == "__main__":
    main()
