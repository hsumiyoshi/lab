#!/usr/bin/env bash
# 成果物をpushする。競合したら取り込んで再試行し、**生成物の衝突は作り直して解く**。
#
# なぜ要るか（2026-08-27の事故）:
#   全11ワークフローに入れたリトライは
#     for i in 1..5: git push || (git pull --rebase --autostash || true)
#   という形で、rebaseが衝突すると `|| true` が握り潰し、リポジトリがrebase途中の
#   まま残る。以降のpushは全部失敗して5回使い切りジョブが落ちる。落ちるので
#   気づけるが、**衝突しているのが生成物なら作り直せば済む**のに人が呼ばれていた。
#
# 方針:
#   - 衝突が生成物だけ → 自分の側を取って build_site.py で作り直し、rebaseを続行
#   - 手書きファイルが1つでも混ざる → rebaseを中止して落とす（人が見るべき）
#   これは「握り潰さない」ための分岐であって、衝突を黙って上書きするものではない。
set -uo pipefail

# build_site.py が書き出すファイル。ここに無いものは手書き扱い
GENERATED=(
  "docs/index.html"
  "docs/forward_pnl.png"
  "docs/latest_anatomy.png"
)

is_generated() {
  local f="$1"
  for g in "${GENERATED[@]}"; do [ "$f" = "$g" ] && return 0; done
  return 1
}

resolve_generated_conflict() {
  local unmerged
  unmerged=$(git diff --name-only --diff-filter=U)
  if [ -z "$unmerged" ]; then
    return 1   # 衝突ではない別の失敗
  fi
  while IFS= read -r f; do
    if ! is_generated "$f"; then
      echo "!! 手書きファイルが衝突: $f —— 自動解決しない"
      return 1
    fi
  done <<< "$unmerged"

  echo "生成物のみの衝突（$(echo "$unmerged" | tr '\n' ' ')）→ 作り直して解決する"
  # 自分の側を採用してから作り直す。どちらを採っても build_site.py の出力で
  # 上書きされるが、rebaseを続けるにはインデックスを解決する必要がある
  while IFS= read -r f; do
    git checkout --theirs -- "$f" 2>/dev/null || git checkout --ours -- "$f" 2>/dev/null || true
    git add -- "$f"
  done <<< "$unmerged"

  if ! python3 build_site.py; then
    echo "!! build_site.py が失敗。作り直せないので中止する"
    return 1
  fi
  # **存在するパスだけを積む。** 1つでも無いパスを混ぜると git add が丸ごと
  # 失敗し、作り直した中身がステージされないまま rebase --continue が
  # 「未解決がある」と言って止まる（2026-08-28に合成テストで踏んだ）
  for g in "${GENERATED[@]}"; do
    [ -e "$g" ] && git add -- "$g"
  done
  GIT_EDITOR=true git rebase --continue
}

for i in 1 2 3 4 5; do
  if git push; then
    exit 0
  fi
  echo "push競合。リモートを取り込んで再試行 ($i/5)"
  if ! git pull --rebase --autostash origin main; then
    if ! resolve_generated_conflict; then
      echo "!! 自動で解決できない衝突。rebaseを中止してジョブを落とす"
      git rebase --abort 2>/dev/null || true
      exit 1
    fi
  fi
  sleep $((i * 3))
done

echo "!! 5回試してpushできなかった"
exit 1
