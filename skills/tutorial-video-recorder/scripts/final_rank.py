#!/usr/bin/env python3
"""最终选曲评分: CLAP margin + AST 标签 + genre 元数据 + 时长适配 + 能量结构。

三个独立证据源:
  ① CLAP 零样本: margin = 正向提示相似度 − 负向提示相似度
  ② AST AudioSet 标签: hot(动感) / cold(悲伤/ambient/小提琴) 计数
  ③ mixkit genre 元数据: house/techno/trance/edm/dubstep... = 硬证据
外加: 时长 ≥ 成片时长(免循环) / LRA(动态) / 能量包络 build 结构
"""
import json
import re
import subprocess

CLAP = {}
for line in open("/tmp/clap_all2.txt"):
    m = re.search(r"^(\d+)\.mp3.*MARGIN=([+-][\d.]+)", line)
    if m:
        CLAP[m.group(1)] = float(m.group(2))

AST = {}
for line in open("/tmp/ast_top50.txt"):
    p = line.strip().split("\t")
    if len(p) >= 4:
        AST[p[0].replace(".mp3", "")] = p[3]

GEN = {}
for f in ("/tmp/mixkit_ids.json", "/tmp/mixkit_hot_ids.json"):
    for k, v in json.load(open(f)).items():
        for i in v:
            GEN.setdefault(i, set()).add(k)

HOT_AST = ("trance music", "techno", "electronic music", "electronic dance music",
           "dance music", "house music", "dubstep", "drum and bass", "breakbeat",
           "electronica", "drum machine", "bass drum", "drum kit", "exciting music",
           "funk", "hip hop", "rock music", "heavy metal", "angry music", "disco")
COLD_AST = ("sad music", "ambient music", "violin", "cello", "slow", "sentimental",
            "ballad", "calm", "soothing", "dreamy", "new-age", "meditation", "mellow",
            "gentle", "relax", "string quartet", "harpsichord", "music of latin",
            "love song", "soft music", "sad")
HOT_GEN = {"house", "edm", "trance", "techno", "dubstep", "drum-and-bass",
           "breakbeat", "electronic", "funk", "rock", "hip-hop", "bass",
           "synthwave", "action", "sports", "groove"}
COLD_GEN = {"cinematic", "ambient", "chillout", "corporate", "cinematic-ambient"}


def dur(p):
    try:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip())
    except Exception:
        return 0.0


rows = []
for f, c in CLAP.items():
    tags = AST.get(f, "")
    tl = tags.lower()
    hot = sum(1 for k in HOT_AST if k in tl)
    cold = sum(1 for k in COLD_AST if k in tl)
    g = GEN.get(f, set())
    gh = len(g & HOT_GEN)
    gc = len(g & COLD_GEN)
    d = dur(f"/tmp/music/{f}.mp3")
    # 综合分: CLAP 为主, AST/genre 为辅, 冷标签重罚
    score = c * 100 + hot * 4 + gh * 3 - cold * 12 - gc * 10
    if d >= 210:
        score += 12          # 免循环
    elif d >= 120:
        score += 4
    rows.append((score, f, c, hot, cold, gh, gc, d, tags, sorted(g)))

rows.sort(reverse=True)
print(f"{'#':>3} {'id':>5} {'score':>7} {'clap':>8} {'hot':>3} {'cold':>4} "
      f"{'gen':>3} {'gc':>2} {'dur':>6}  genre / tags")
for i, (s, f, c, hot, cold, gh, gc, d, tags, g) in enumerate(rows[:30], 1):
    print(f"{i:>3} {f:>5} {s:>7.1f} {c:+8.4f} {hot:>3} {cold:>4} {gh:>3} {gc:>2} "
          f"{d:>6.1f}  {'/'.join(g)[:26]:26} {tags[:70]}")

json.dump([{"id": f, "score": s, "clap": c, "hot": hot, "cold": cold,
            "gen": g, "dur": d, "tags": tags} for s, f, c, hot, cold, gh, gc, d, tags, g in rows],
          open("/tmp/final_ranking.json", "w"), ensure_ascii=False, indent=1)
