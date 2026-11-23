#!/usr/bin/env python3
"""抓 mixkit 的**动感 genre** 曲库 id。

🚨 上次的根因: 候选池只覆盖 epic/cinematic/technology/upbeat/trailer/dramatic
   —— 全是"电影感/企业科技", 天然不激情。选出的 594 号就是 cinematic+dramatic
   → 听起来像 corporate 励志垃圾。
   → 必须把 techno / drum-and-bass / house / electronic / dance / rock 这些
      真正带劲的 genre 补进池子。
"""
import json
import pathlib
import re
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

# 动感/带劲的 genre 关键词
HOT_GENRES = [
    "techno", "drum-and-bass", "drum-and-bass-2", "house", "electronic",
    "trance", "dubstep", "edm", "dance", "rave", "hardstyle", "garage",
    "progressive-house", "breakbeat", "funk", "rock", "hip-hop", "pop",
    "groove", "bass", "punk", "indie", "synthwave", "sports", "action",
]


def fetch(url, timeout=45, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 4 * (i + 1)
                print(f"  429 → 退避 {wait}s {url}")
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code} {url}")
            return None
        except Exception as e:
            print(f"  ERR {type(e).__name__} {url}")
            time.sleep(2)
    return None


ID_RE = re.compile(r"/music/(\d+)/")


def ids_from(html):
    return sorted(set(ID_RE.findall(html)))


def main():
    catalog = {}
    for g in HOT_GENRES:
        url = f"https://mixkit.co/free-stock-music/{g}/"
        html = fetch(url)
        if not html:
            continue
        ids = ids_from(html)
        if not ids:
            # 可能是无效 genre → 试 /free-stock-music/<g>/
            html = fetch(f"https://mixkit.co/free-stock-music/{g}")
            ids = ids_from(html or "")
        if ids:
            catalog[g] = ids
            print(f"{g:22} n={len(ids):3}  {ids[:6]}")
        time.sleep(1.2)          # 别把 mixkit 打挂
    json.dump(catalog, open("/tmp/mixkit_hot_ids.json", "w"), indent=1)
    allids = sorted({i for v in catalog.values() for i in v})
    print(f"\n总候选 {len(allids)} 首, 新 genre {len(catalog)} 个")
    json.dump(allids, open("/tmp/hot_ids.json", "w"))


if __name__ == "__main__":
    main()
