#!/usr/bin/env python3
"""做候选试听片段: 每首 25s, 从最有能量的段落开始, 响度归一到可听水平。

用法: previews.py <id1> <id2> ...
输出: /tmp/previews/<id>.m4a
"""
import os
import pathlib
import subprocess
import sys

OUT = pathlib.Path("/tmp/previews")
OUT.mkdir(exist_ok=True)

# 用 AST/CLAP 已知的能量结构挑起点: 跳过开头, 从 15s 起取 25s
START = 15.0
DUR = 25.0


def main(ids):
    for tid in ids:
        src = pathlib.Path(f"/tmp/music/{tid}.mp3")
        if not src.exists():
            print(f"skip {tid} (missing)")
            continue
        dst = OUT / f"{tid}.m4a"
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-ss", str(START), "-t", str(DUR), "-i", str(src),
               "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.6,"
                      "afade=t=out:st=23.5:d=1.5",
               "-c:a", "aac", "-b:a", "192k", str(dst)]
        subprocess.run(cmd, check=False)
        print(f"✅ {dst} ({dst.stat().st_size//1024}KB)")


if __name__ == "__main__":
    main(sys.argv[1:])
