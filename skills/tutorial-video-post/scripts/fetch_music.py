#!/usr/bin/env python3
"""BGM 曲库抓取 —— 从免费可商用曲库按 genre 批量下载候选曲。

🚨 为什么必须按 genre 分池抓(别一步到位直接抓"热门"):
    只抓 epic/cinematic/technology/upbeat/trailer/dramatic 这些 genre →
    候选池全是"电影感/企业科技" → 选出来的必然是 corporate 励志垃圾。
    实测被选中的曲子 genre = cinematic+dramantic, 用户听完直接骂"垃圾配乐"。
    → 必须把 techno / house / drum-and-bass / edm / rock 等**动感 genre** 补进池子。

用法:
    python fetch_music.py --out music_pool --genres techno,house,edm --per-genre 25
    python fetch_music.py --out music_pool --genres techno --per-genre 5 --dry-run
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

# 动感/带劲的 genre 关键词(曲库 URL slug)
HOT_GENRES = [
    "techno", "drum-and-bass", "drum-and-bass-2", "house", "electronic",
    "trance", "edm", "dubstep", "breakbeat", "funk", "rock", "hip-hop",
    "bass", "groove", "synthwave", "action", "sports", "dance",
]

# 排除的 genre(电影感/悲伤/企业感) —— 除非明确要那种风格
COLD_GENRES = [
    "cinematic", "ambient", "chillout", "corporate", "sad", "calm",
    "emotional", "motivational", "inspiring",
]

# 曲库下载 URL 模板(占位符由实际曲库替换)
# ⚠ 真实形态: 曲目 id 是**三位数**(如 134/162/160), 同一 id 在多页会重复出现。
BASE_URL = "https://assets.{domain}/music/{track_id}/{track_id}.mp3"
LIST_URL = "https://{domain}/free-stock-music/{genre}/"
# 从列表页 HTML 里提取 mp3 链接(域名不写死, 只认 /music/<id>/<id>.mp3 形态)
MP3_RE = re.compile(r"/music/(\d+)/\1\.mp3")


def log(msg):
    print(f"[fetch] {msg}", flush=True)


def fetch_list(domain, genre, timeout=25):
    """抓某 genre 的曲目 id 列表。返回 id 列表。
    ⚠ 该曲库的列表页**不认 ?page= 参数**(实测 page=1/2/3 内容完全一样),
       每页只暴露首批曲目 → **靠多 genre 并集**来扩池子, 别指望翻页。
    ⚠ 同一 id 在多 genre 间会重复 → 全局去重保序。
    """
    url = LIST_URL.format(domain=domain, genre=genre)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "ignore")
    ids, seen = [], set()
    for t in MP3_RE.findall(html):
        if t not in seen:
            seen.add(t)
            ids.append(t)
    return ids


def download(domain, track_id, dest, timeout=60):
    url = BASE_URL.format(domain=domain, track_id=track_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if len(data) < 20000:          # 太小的多半是错误页
        raise ValueError(f"payload too small ({len(data)}B)")
    dest.write_bytes(data)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--genres", default="", help="逗号分隔的 genre slug(默认用内置动感池)")
    ap.add_argument("--domain", default="mixkit.co", help="曲库域名")
    ap.add_argument("--per-genre", type=int, default=20, help="每个 genre 抓多少首")
    ap.add_argument("--sleep", type=float, default=1.2, help="每个 genre 之间的间隔秒数")
    ap.add_argument("--dry-run", action="store_true", help="只列 id 不下载")
    args = ap.parse_args()

    genres = [g.strip() for g in args.genres.split(",") if g.strip()] or HOT_GENRES
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = {"domain": args.domain, "genres": {}, "colds": COLD_GENRES}
    total_ok = 0
    global_seen = set()          # 全局去重: 同一 id 在多 genre 重复出现
    for gi, genre in enumerate(genres):
        if genre in COLD_GENRES:
            log(f"跳过冷门 genre: {genre}")
            continue
        try:
            ids = fetch_list(args.domain, genre)
        except Exception as e:
            log(f"genre {genre} 列表抓取失败: {e}")
            manifest["genres"][genre] = {"ok": 0, "err": str(e)}
            continue
        ids = [t for t in ids if t not in global_seen][:args.per_genre]
        global_seen.update(ids)
        log(f"genre {genre}: {len(ids)} 首")
        got = 0
        for tid in ids:
            dest = out / f"{genre}_{tid}.mp3"
            if dest.exists():
                got += 1
                continue
            if args.dry_run:
                log(f"  (dry-run) {tid} -> {dest.name}")
                got += 1
                continue
            try:
                n = download(args.domain, tid, dest)
                got += 1
                log(f"  ✅ {dest.name} {n//1024}KB")
            except Exception as e:
                log(f"  ❌ {tid}: {e}")
                # 429/限流 → 退避
                if "429" in str(e) or "Too Many" in str(e):
                    time.sleep(args.sleep * 6)
            time.sleep(0.25)
        manifest["genres"][genre] = {"ids": ids, "ok": got}
        total_ok += got
        if gi < len(genres) - 1:
            time.sleep(args.sleep)

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成: 候选 {total_ok} 首 -> {out}")
    log(f"清单: {out/'manifest.json'}")
    if total_ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
