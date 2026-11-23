#!/usr/bin/env python3
"""用新配乐重混已验收的 silent.mp4 → final_v7.mp4

视频流 -c:v copy → 字幕/画面像素零损失。
音频链与 skill compose.py 完全一致(闪避 + 限幅 + loudnorm)。
"""
import pathlib
import subprocess
import sys

FF = "ffmpeg"
silent = pathlib.Path("/tmp/vid5/out/silent.mp4")
bgm = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/vid5/music/bgm_new.mp3")
gain = sys.argv[2] if len(sys.argv) > 2 else "-15.0"
offset = sys.argv[3] if len(sys.argv) > 3 else "0.0"
out = pathlib.Path(sys.argv[4] if len(sys.argv) > 4 else "/tmp/vid5/out/final_v7.mp4")

total_dur = float(subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "csv=p=0", str(silent)], capture_output=True, text=True).stdout.strip())

moff = float(offset)
af = (f"[1:a]atrim=start={moff:.3f}:end={moff + total_dur:.3f},"
      f"asetpts=N/SR/TB,volume={gain}dB,afade=t=in:st=0:d=1.2,"
      f"afade=t=out:st={max(0.0, total_dur - 2.2):.3f}:d=2.2[bg];"
      f"[0:a]asplit=2[va][sc];"
      f"[bg][sc]sidechaincompress=threshold=0.02:ratio=12:"
      f"attack=8:release=350:makeup=1[bgd];"
      f"[va][bgd]amix=inputs=2:duration=first:dropout_transition=0:"
      f"weights=1 1,"
      f"alimiter=limit=0.95,loudnorm=I=-16:TP=-1.5:LRA=11,"
      f"aresample=44100[a]")

cmd = [FF, "-y", "-loglevel", "error",
       "-i", str(silent),
       "-stream_loop", "-1", "-i", str(bgm),
       "-filter_complex", af, "-map", "0:v", "-map", "[a]",
       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
       "-movflags", "+faststart", str(out)]
print(" ".join(cmd))
subprocess.run(cmd, check=True)
print(f"✅ {out} {out.stat().st_size // 1024 // 1024}MB  {total_dur:.2f}s")

# 响度实测
r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", str(out),
                    "-af", "loudnorm=print_format=summary", "-f", "null", "-"],
                   capture_output=True, text=True).stderr
for line in r.splitlines():
    if "LUFS" in line or "Peak" in line:
        print("   ", line.strip())
