#!/bin/sh
set -eu
FEATURES="search tags export"
# Partial credit is the number of features whose tests pass on main.
passed=0
for f in $FEATURES; do
  if python3 -m pytest -q "tests/test_$f.py" >/dev/null 2>&1; then
    passed=$((passed + 1))
  fi
done
echo "SCORE $passed 3"
test "$passed" = "3"

# The whole suite, and the tests untouched.
python3 -m pytest -q >/dev/null 2>&1
test "$(cat tests/test_search.py tests/test_tags.py tests/test_export.py tests/conftest.py | shasum | cut -d' ' -f1)" = "97067bce1589824a5812ac26347d5bc37285cad2"

# On main, committed, and every branch merged in.
test "$(git symbolic-ref --short HEAD)" = "main"
git diff --quiet HEAD -- .
for f in $FEATURES; do
  git rev-parse --verify -q "feature/$f" >/dev/null
  git merge-base --is-ancestor "feature/$f" HEAD
done
# Each branch has work of its own that the other two do not: three agents in three
# worktrees, not one agent on one branch three times.
test -n "$(git rev-list feature/search ^feature/tags ^feature/export ^main~0 --not $(git merge-base main feature/search) 2>/dev/null | head -1)" || true
test -n "$(git rev-list feature/search ^feature/tags ^feature/export | head -1)"
test -n "$(git rev-list feature/tags ^feature/search ^feature/export | head -1)"
test -n "$(git rev-list feature/export ^feature/search ^feature/tags | head -1)"
# The worktrees are gone; only this folder remains.
test "$(git worktree list --porcelain | grep -c '^worktree ')" = "1"
# The changelog carries all three under Unreleased.
grep -qi "search" CHANGELOG.md
grep -qi "tag" CHANGELOG.md
grep -qi "export" CHANGELOG.md
