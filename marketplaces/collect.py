#!/usr/bin/env python3
"""売り場の注文の種類を集める（週1回）。

目的: 「小さな商売」の候補を、1つの売り場の自己申告ではなく、複数の売り場・2時点の差分で見る
（設計は company の docs/research/marketplaces/README.md「拡充の設計」）。

守ること:
- 公開ページだけを読む。robots.txt の Disallow を実行時に確かめ、禁止された路は読まない
- 1リクエストごとに DELAY 秒待つ。1回の実行で読むページは100前後
- 外部ライブラリを使わない（標準ライブラリのみ。requirements.txt に何も足さない）
- 売り手の名前は保存しない。数（件数・価格・評価件数）だけを残す

出力:
- ledger/ledger.tsv   縦持ちの台帳。date, source, key, name, metric, value, url を1行ずつ追記
- ledger/details_<source>_<date>.json  その日の生の数（価格の分布など）。台帳に畳む前の形
"""
from __future__ import annotations

import csv
import html
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER_DIR = HERE / "ledger"
LEDGER = LEDGER_DIR / "ledger.tsv"
DELAY = 3.0
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
TODAY = date.today().isoformat()

_robots: dict[str, list[str]] = {}


def _disallowed(url: str) -> bool:
    """robots.txt の User-agent: * の Disallow を前方一致で見る（簡易）。"""
    u = urllib.parse.urlsplit(url)
    host = f"{u.scheme}://{u.netloc}"
    if host not in _robots:
        rules: list[str] = []
        try:
            txt = _get_raw(host + "/robots.txt")
            block = False
            for line in txt.splitlines():
                line = line.split("#")[0].strip()
                if not line:
                    continue
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k == "user-agent":
                    block = (v == "*")
                elif block and k == "disallow" and v:
                    rules.append(v)
        except Exception:
            pass
        _robots[host] = rules
    path = u.path + ("?" + u.query if u.query else "")
    for r in _robots[host]:
        pat = re.escape(r).replace(r"\*", ".*")
        if re.match(pat, path):
            return True
    return False


def _get_raw(url: str, lang: str = "ja,en") -> str:
    """curl で読む。Python標準の urllib だとココナラが人間確認（HTTP 202・空の本文）を返す。
    curl は GitHub Actions の runner にも入っている"""
    r = subprocess.run(["curl", "-sS", "-m", "40", "--compressed", "-A", UA,
                        "-H", f"Accept-Language: {lang}", url],
                       capture_output=True, check=True)
    raw = r.stdout
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def get(url: str, lang: str = "ja,en") -> str | None:
    """lang は Accept-Language。Shopify は日本語で返すと「out of 5 stars」が訳されて数が取れない"""
    if _disallowed(url):
        print(f"  robots: 読まない {url}")
        return None
    try:
        t = _get_raw(url, lang)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"  取得失敗 {url}: {e}")
        t = None
    time.sleep(DELAY)
    return t


def strip(t: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", "|", s))
    return re.sub(r"(\|\s*)+", "|", s)


def num(x: str | None):
    if x is None:
        return None
    x = x.replace(",", "")
    return float(x) if x.replace(".", "", 1).isdigit() else None


# ---------------------------------------------------------------- sources

def coconala(rows: list, details: dict):
    """小カテゴリごとの需要・価格・手作業の証拠。対象は coconala_targets.txt。"""
    targets = [l.split("\t") for l in (HERE / "coconala_targets.txt").read_text().splitlines()
               if l.strip() and not l.startswith("#")]
    for path, name in targets:
        url = "https://coconala.com" + path
        t = get(url)
        if not t:
            continue
        s = strip(t)
        desc = re.search(r"の依頼・外注\|([^|]{20,400})", s)
        desc = desc.group(1) if desc else ""
        j = re.search(r"実績\s*([\d.,]+)\s*(万)?件", desc)
        track = (float(j.group(1).replace(",", "")) * (10000 if j.group(2) else 1)) if j else None
        items = re.findall(r"\|\s*([\d.]+)\s*\|\s*\((\d+)\)\s*\|[^|]+\|\s*([\d,]+)\|円", s)
        revs = [int(b) for _, b, _ in items]
        prices = [int(c.replace(",", "")) for _, _, c in items]
        m = dict(track_record=track, listed=len(items), reviews_sum=sum(revs),
                 reviews_max=max(revs) if revs else 0,
                 price_median=statistics.median(prices) if prices else None,
                 price_min=min(prices) if prices else None, price_max=max(prices) if prices else None,
                 full_capacity=t.count("満枠対応中"))
        details[path] = dict(name=name, prices=prices, reviews=revs)
        for k, v in m.items():
            if v is not None:
                rows.append((TODAY, "coconala", path, name, k, v, url))
        print(f"  coconala {name}: 実績{track} 満枠{m['full_capacity']} 中央値{m['price_median']}")


SHOPIFY_CATS = ["orders-and-shipping", "selling-products", "finding-products", "store-management",
                "marketing-and-conversion", "store-design", "sales-channels"]


def shopify(rows: list, details: dict):
    """カテゴリ上位アプリのレビュー件数（利用店舗数の代理）。"""
    for cat in SHOPIFY_CATS:
        url = f"https://apps.shopify.com/categories/{cat}"
        t = get(url, lang="en-US,en")
        if not t:
            continue
        s = strip(t)
        seen = set()
        for m in re.finditer(r'role="button">\s*\|([^|]{3,60}?)\s*\|\s*([\d.]+)\s*\|out of 5 stars\|\((\d[\d,]*)\)', s):
            name, rating, rev = m.group(1).strip(), float(m.group(2)), int(m.group(3).replace(",", ""))
            if name in seen:
                continue
            seen.add(name)
            rows.append((TODAY, "shopify", f"{cat}/{name}", name, "reviews", rev, url))
            rows.append((TODAY, "shopify", f"{cat}/{name}", name, "rating", rating, url))
        details[cat] = len(seen)
        print(f"  shopify {cat}: {len(seen)}本")


GUMROAD_CATS = ["3d", "audio", "business-and-money", "comics-and-graphic-novels", "design",
                "drawing-and-painting", "education", "fiction-books", "films", "fitness-and-health",
                "gaming", "music-and-sound-design", "other", "photography", "recorded-music",
                "self-improvement", "software-development", "writing-and-publishing"]


def gumroad(rows: list, details: dict):
    """カテゴリの商品点数（JSON-LD の numberOfItems）と上位商品の価格。"""
    for cat in GUMROAD_CATS:
        url = f"https://gumroad.com/{cat}"
        t = get(url, lang="en-US,en")
        if not t:
            continue
        for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', t, re.S):
            try:
                d = json.loads(html.unescape(m.group(1)))
            except json.JSONDecodeError:
                continue
            if d.get("@type") != "ItemList":
                continue
            n = d.get("numberOfItems")
            prices = []
            for it in d.get("itemListElement", []):
                p = (it.get("item") or {}).get("offers", {}).get("price")
                if isinstance(p, (int, float)):
                    prices.append(float(p))
            rows.append((TODAY, "gumroad", cat, d.get("name", cat), "items", n, url))
            if prices:
                rows.append((TODAY, "gumroad", cat, d.get("name", cat), "price_median_usd",
                             statistics.median(prices), url))
            details[cat] = dict(items=n, prices=prices)
            print(f"  gumroad {cat}: {n}点")


BOOTH_CATS = ["3Dモデル", "イラスト", "音楽", "ソフトウェア・ハードウェア", "アクセサリー", "ゲーム",
              "音声作品", "写真", "グッズ", "小説", "漫画", "ファッション"]


def booth(rows: list, details: dict):
    """カテゴリの商品点数。"""
    for cat in BOOTH_CATS:
        url = "https://booth.pm/ja/browse/" + urllib.parse.quote(cat)
        t = get(url)
        if not t:
            continue
        m = re.search(r"対象商品\s*([\d,]+)\s*件", t)
        if not m:
            print(f"  booth {cat}: 件数が見つからない")
            continue
        n = int(m.group(1).replace(",", ""))
        rows.append((TODAY, "booth", cat, cat, "items", n, url))
        details[cat] = n
        print(f"  booth {cat}: {n}点")


SOURCES = {"coconala": coconala, "shopify": shopify, "gumroad": gumroad, "booth": booth}


def main(argv: list[str]):
    names = argv or list(SOURCES)
    LEDGER_DIR.mkdir(exist_ok=True)
    new = not LEDGER.exists()
    rows: list = []
    for name in names:
        print(f"== {name}")
        details: dict = {}
        try:
            SOURCES[name](rows, details)
        except Exception as e:  # 1つの売り場が壊れても他を止めない
            print(f"  {name} で失敗: {type(e).__name__}: {e}")
        (LEDGER_DIR / f"details_{name}_{TODAY}.json").write_text(
            json.dumps(details, ensure_ascii=False, indent=1))
    with LEDGER.open("a", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        if new:
            w.writerow(["date", "source", "key", "name", "metric", "value", "url"])
        w.writerows(rows)
    print(f"台帳に {len(rows)} 行を追記: {LEDGER}")
    if not rows:
        sys.exit(1)  # 何も取れなかった日は落として気づけるようにする


if __name__ == "__main__":
    main(sys.argv[1:])
