#!/usr/bin/env python3
"""収集の共通ランタイム（2026-08-24・Haruki指示「収集定義とアラート検知を仕組み化」）。

**なぜ作るか**: 新しい収集を書くたびに同じ種類の穴が開いていた——
data/のgitignore漏れ3回・依存の宣言漏れ・NASAへのIPv6到達不能・パーサ破損・生情報の取り忘れ。
書き手の質ではなく**共通の骨格が無い**ことが原因なので、踏んだ穴だけをここに1回実装する。

**作らないもの**: 汎用ETL・スケジューラ・ワークフローエンジン・UI・DB。
GitHub Actionsとgitで足りている部分は置き換えない。

新しい収集の追加に必要なのは「宣言（sources.yaml相当のdict）＋パーサ関数1つ」だけ。
"""

import gzip
import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
UA_DEFAULT = "Mozilla/5.0 (personal research ledger; https://github.com/hsumiyoshi/lab)"
_last_fetch = {}          # ホストごとの最終取得時刻（レート制限）


class CollectError(Exception):
    """収集の失敗。呼び出し側は握りつぶさず、障害ログに残して非ゼロ終了する。"""


def _force_ipv4_once():
    """NASA(EOSDIS)等はIPv6を返すがGitHub RunnerはIPv6を出られない（2026-08-23に実測）。"""
    if getattr(_force_ipv4_once, "done", False):
        return
    orig = socket.getaddrinfo
    socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: orig(h, p, socket.AF_INET, t, pr, fl)
    _force_ipv4_once.done = True


def fetch(url: str, *, interval: float = 1.5, retries: int = 4, ipv4: bool = False,
          ua: str = UA_DEFAULT, timeout: int = 40, encoding: str = "utf-8") -> str:
    """礼儀つきGET。同一ホストへは interval 秒以上あける。429はRetry-Afterを尊重。"""
    if ipv4:
        _force_ipv4_once()
    host = url.split("/")[2]
    wait = interval - (time.time() - _last_fetch.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            _last_fetch[host] = time.time()
            return raw.decode(encoding, errors="replace")
        except urllib.error.HTTPError as e:
            if attempt == retries - 1:
                raise CollectError(f"{url}: HTTP {e.code}") from e
            if e.code == 429:
                w = int(e.headers.get("Retry-After") or 0) or 30 * (2 ** attempt)
                print(f"  429 → {min(w,300)}秒待機")
                time.sleep(min(w, 300))
            else:
                time.sleep(2 ** (attempt + 1))
        except Exception as e:
            if attempt == retries - 1:
                raise CollectError(f"{url}: {type(e).__name__} {e}") from e
            time.sleep(2 ** (attempt + 1))


def save_raw(source: str, payload, *, date: str = None) -> Path:
    """生情報の保存。**装飾は捨て、構造化された中身を残す**（Haruki方針 2026-08-24）。

    一回性のあるページ（当日しか出ない・巻き取りで消える）は、抽出後の数値だけ残しても
    後から問い直せない。再取得可能なページには使わない（容量の無駄）。
    """
    d = ROOT / "collector" / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{date or datetime.now(JST):%Y-%m-%d}.json" if date is None else d / f"{date}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(f)          # 原子的に置き換え（途中で落ちても壊れたファイルを残さない）
    return f


def append_ledger(source: str, key: str, record: dict) -> bool:
    """追記型の台帳。key で重複排除する。新規に追加したら True。"""
    f = ROOT / "collector" / "data" / f"{source}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    hist = json.loads(f.read_text()) if f.exists() else {}
    fresh = key not in hist
    if fresh:
        hist[key] = record
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(f)
    return fresh


def check_expectations(source: str, rows: list, expect: dict) -> list:
    """アラート検知: 件数レンジとスキーマの逸脱を返す（空なら正常）。

    「静かに壊れる」経路を塞ぐのが目的——**件数がゼロでも成功扱いになる**のが最悪。
    """
    problems = []
    lo, hi = expect.get("rows", [1, 10**9])
    if not (lo <= len(rows) <= hi):
        problems.append(f"{source}: 件数が想定外 {len(rows)}件（想定 {lo}〜{hi}）"
                        f"——パーサ破損かページ改変の可能性")
    need = set(expect.get("schema", []))
    if rows and need:
        missing = need - set(rows[0])
        if missing:
            problems.append(f"{source}: 列が欠けている {sorted(missing)}——ページ構造が変わった可能性")
    return problems


def report_incident(source: str, messages: list) -> None:
    """障害を台帳に残す。**手で埋めない**（2026-08-23の規律）。"""
    if not messages:
        return
    f = ROOT / "docs" / "incidents.md"
    f.parent.mkdir(exist_ok=True)
    with f.open("a", encoding="utf-8") as fp:
        for m in messages:
            fp.write(f"| {datetime.now(JST):%Y-%m-%dT%H:%MZ} | collector/{source} | — | {m} |\n")


def run(spec: dict, parser) -> dict:
    """宣言 + パーサ関数 = 収集1本。ガードは全部ここから降ってくる。

    spec: {name, url, fetch:{}, raw:{save,bool}, expect:{rows,schema}, key: 関数}
    parser: (text) -> list[dict]
    """
    name = spec["name"]
    print(f"[{name}] 取得: {spec['url']}")
    text = fetch(spec["url"], **spec.get("fetch", {}))
    rows = parser(text)
    problems = check_expectations(name, rows, spec.get("expect", {}))
    if spec.get("raw", {}).get("save"):
        save_raw(name, {"fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
                        "source": spec["url"], "items": rows})
    keyf = spec.get("key") or (lambda r: r.get("id") or hashlib.sha1(
        json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12])
    added = sum(1 for r in rows if append_ledger(name, keyf(r), r))
    print(f"[{name}] {len(rows)}件取得 / 新規 {added}件" + (f" / ⚠ {len(problems)}件の異常" if problems else ""))
    report_incident(name, problems)
    if problems:
        raise CollectError("; ".join(problems))
    return {"fetched": len(rows), "added": added}
