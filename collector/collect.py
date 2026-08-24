#!/usr/bin/env python3
"""収集の実行入口。`python3 collector/collect.py [名前...]`（省略で全ソース）。

失敗しても他のソースは続行し、**最後にまとめて非ゼロ終了**する（1本の失敗で全部止めない）。
"""
import sys
from runtime import CollectError, run
from sources import SOURCES

names = sys.argv[1:] or list(SOURCES)
failed = []
for n in names:
    spec = SOURCES.get(n)
    if not spec:
        print(f"!! 未知のソース: {n}")
        failed.append(n)
        continue
    try:
        run(spec, spec["parser"])
    except CollectError as e:
        print(f"!! {n} 失敗: {e}")
        failed.append(n)
if failed:
    sys.exit(f"失敗 {len(failed)}件: {failed}")
print("全ソース正常")
