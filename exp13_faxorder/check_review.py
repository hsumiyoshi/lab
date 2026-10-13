#!/usr/bin/env python3
"""確認画面（review.html）を headless Chromium で実際に操作して、守ることが守られているかを確かめる。

確かめること
1. 要確認・不明の行は品番が空（「選んでください」）で始まる
2. 未確認の行が残るあいだはCSVを書き出せず、どの行が残っているかを出す
3. 「一覧から探す」で、候補に無い品を一覧全体から探して選べる
4. 選ぶ・直すと、判定のバッジと「n行が要確認」がその場で変わる
5. 行の追加・削除ができ、すべて確かめるとCSVを書き出せる

使い方: python3 check_review.py out/fujipanko/rev/review.html --search 白生 --pick FP-011 [--row 1]
       （--row は探す行。既定は、候補に --pick が入っていない最初の要確認・不明の行）
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--search", required=True)
    ap.add_argument("--pick", required=True)
    ap.add_argument("--row", type=int, default=0)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    results: list[tuple[str, bool, str]] = []

    def ok(name: str, cond: bool, detail: str = ""):
        results.append((name, bool(cond), detail))

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        pg = b.new_page(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(Path(a.html).resolve().as_uri())
        pg.wait_for_timeout(300)
        rows = pg.evaluate("ROWS")
        trs = pg.locator("#rows tr")
        n = trs.count()
        ok("行の数が読み取り結果と同じ", n == len(rows), f"{n}行")

        # 1. 要確認・不明の行は品番が空（「選んでください」）で始まる
        bad = []
        for i, r in enumerate(rows):
            sel = trs.nth(i).locator("select.code")
            v = sel.input_value()
            if r["判定"] != "一致" and (v != "" or sel.locator("option").first.inner_text() != "選んでください"):
                bad.append(i + 1)
        need0 = [i + 1 for i, r in enumerate(rows) if r["判定"] != "一致"]
        empty0 = [i + 1 for i in range(n) if trs.nth(i).locator("select.code").input_value() == ""]
        ok("要確認・不明の行は「選んでください」（空）で始まる", not bad,
           f"要確認・不明の行 {need0}／品番が空で始まった行 {empty0}")
        sheet_warn = pg.locator(".sheetwarn").count() > 0

        def count_text() -> str:
            return pg.locator("#count").inner_text()

        c0 = count_text()
        ok("最初の「要確認」の数が判定と合う", f"{len(need0)}行が要確認" in c0, c0)

        # 2. 未確認が残るあいだは書き出せない
        got_dl = []
        pg.on("download", lambda d: got_dl.append(d))
        pg.click("#dl")
        pg.wait_for_timeout(300)
        msg = pg.locator("#msg").inner_text()
        listed = [int(x) for x in re.findall(r"\d+", msg.split("行目")[0])] if "行目" in msg else []
        ok("未確認が残るあいだはCSVを書き出せない", not got_dl and "書き出せません" in msg, msg)
        ok("書き出せない理由に残りの行番号が出る", listed == need0, f"出た行 {listed}")

        # 3. 一覧から探す: 候補に無い品を探して選ぶ
        row = a.row - 1 if a.row else next(
            (i for i, r in enumerate(rows) if not r["品番"] and a.pick not in r["候補"]),
            next(i for i, r in enumerate(rows) if not r["品番"]))
        in_cands = a.pick in rows[row]["候補"]
        tr = trs.nth(row)
        tr.locator("input.find").fill(a.search)
        pg.wait_for_timeout(200)
        hits = tr.locator(".hits button")
        labels = [hits.nth(k).inner_text() for k in range(hits.count())]
        ok(f"{row + 1}行目で「{a.search}」を探すと {a.pick} が出る（候補に{'あった' if in_cands else '無かった'}品）",
           any(l.startswith(a.pick + " ") for l in labels), " / ".join(labels))
        before = count_text()
        badge_before = tr.locator(".st .b").inner_text()
        hits.filter(has_text=a.pick).first.click()
        pg.wait_for_timeout(200)
        tr = pg.locator("#rows tr").nth(row)
        v = tr.locator("select.code").input_value()
        after = count_text()
        ok(f"探した品を押すと品番が {a.pick} になる", v == a.pick, v)
        why_after = tr.locator(".st .why").inner_text() if tr.locator(".st .why").count() else ""
        rest = ""
        if tr.locator("button.ack").count() or why_after:  # 数量・納期の点も残っている行は、それも片づけてから数える
            rest = f"（品番を選んだ時点の残り: {why_after.replace(chr(10), ' ')}）"
            q = tr.locator("input.qty")
            if q.evaluate("e=>e.classList.contains('flagged')"):
                q.fill("1")
            if tr.locator("button.ack").count():
                tr.locator("button.ack").click()
                pg.wait_for_timeout(50)
            d = tr.locator("input.due")
            if d.evaluate("e=>e.classList.contains('flagged')"):  # 読めない納期は「確認した」では消えない。直す
                d.fill("")
            pg.wait_for_timeout(100)
            tr = pg.locator("#rows tr").nth(row)
            after = count_text()
        badge_after = tr.locator(".st .b").inner_text()
        nb, na = (int(re.search(r"(\d+)行が要確認", x).group(1)) for x in (before, after))
        ok("選ぶ・直すとバッジと要確認の数がその場で変わる", na == nb - 1 and badge_after == "確認済み",
           f"{before} → {after}／バッジ {badge_before} → {badge_after}{rest}")

        # 4. 残りを片づける: 候補から選ぶ・数量を直す・「確認した」
        for i in range(pg.locator("#rows tr").count()):
            tr = pg.locator("#rows tr").nth(i)
            if tr.locator("select.code").input_value() == "":
                chip = tr.locator(".cands button").first
                if chip.count():
                    chip.click()
                else:
                    tr.locator("select.code").select_option(index=1)
                pg.wait_for_timeout(50)
                tr = pg.locator("#rows tr").nth(i)
            q = tr.locator("input.qty")
            if q.evaluate("e=>e.classList.contains('flagged')"):
                q.fill(str(rows[i]["数量"] or 1) if (rows[i]["数量"] or 0) < 1000 else "1")
                pg.wait_for_timeout(50)
            ack = tr.locator("button.ack")
            if ack.count():
                ack.click()
                pg.wait_for_timeout(50)
            d = tr.locator("input.due")
            if d.evaluate("e=>e.classList.contains('flagged')"):
                d.fill("")
                pg.wait_for_timeout(50)
        c1 = count_text()
        ok("すべて確かめると要確認が0になる", "0行が要確認" in c1, c1)

        # 5. 行の追加と削除
        pg.click("#add")
        pg.wait_for_timeout(100)
        c2 = count_text()
        ok("行を追加すると、その行が要確認になる", f"{n + 1}行中 1行が要確認" in c2, c2)
        pg.click("#dl")
        pg.wait_for_timeout(200)
        msg2 = pg.locator("#msg").inner_text()
        ok("追加した行が空のままでは書き出せない", not got_dl and f"{n + 1}行目" in msg2, msg2)
        last = pg.locator("#rows tr").last
        last.locator("textarea.name").fill("電話の注文")
        last.locator("input.find").fill(a.search)
        pg.wait_for_timeout(100)
        last.locator(".hits button").first.click()
        pg.wait_for_timeout(100)
        last = pg.locator("#rows tr").last
        last.locator("input.qty").fill("3")
        last.locator("input.due").fill("10月20日")
        pg.wait_for_timeout(100)
        iso = last.locator(".iso").inner_text()
        ok("納期「10月20日」を日付に直して見せる", re.search(r"\d{4}-10-20", iso) is not None, iso)
        pg.locator("#rows tr").first.locator("button.del").click()
        pg.wait_for_timeout(100)
        c3 = count_text()
        ok("行を削除すると行数が減る", f"{n}行中" in c3, c3)
        if sheet_warn:
            pg.click("#dl")
            pg.wait_for_timeout(200)
            ok("用紙ごと要確認は、用紙の確認に印を付けるまで書き出せない", not got_dl, pg.locator("#msg").inner_text())
            pg.check("#sheetack")
        with pg.expect_download(timeout=3000) as dl:
            pg.click("#dl")
        path = dl.value.path()
        csv_text = Path(path).read_text(encoding="utf-8-sig")
        lines = csv_text.strip().splitlines()
        ok("すべて確かめるとCSVを書き出せる", len(lines) == n + 1, f"{len(lines) - 1}行: {lines[-1]}")
        ok("ページの中でエラーが出ない", not errors, "; ".join(errors))
        b.close()
    width = max(len(r[0]) for r in results)
    for name, good, detail in results:
        print(f"{'OK ' if good else 'NG '} {name.ljust(width)}  {detail}")
    print(json.dumps({"passed": sum(r[1] for r in results), "total": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
