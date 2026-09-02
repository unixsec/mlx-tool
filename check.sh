#!/bin/bash
# 提交前必跑。把「记得跑复检」变成一条命令（归档 checklist 第 8 条）。
set -uo pipefail
cd "$(dirname "$0")"
# **venv 自己找，不假设布局**。它可能在产品根（扁平仓库）或产品根的
# 上一级（`v7/` 布局的开发仓库）。写死任一边，另一边就跑不了 ——
# 2026-09-02 扁平化时踩过：改成 `$(pwd)` 之后源仓库的 pre-commit 当场断。
VENV=""
for _c in "$(pwd)/.venv" "$(cd .. && pwd)/.venv"; do
  [ -x "$_c/bin/python3" ] && { VENV="$_c/bin"; break; }
done
[ -n "$VENV" ] || { echo "找不到 .venv（产品根与其上一级都没有）" >&2; exit 3; }
fail=0

step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

step "ruff（target-version=py39）"
"$VENV/ruff" check . || fail=1

step "pytest"
"$VENV/pytest" tests -q || fail=1

step "3.9.6 下限编译（真解释器，不是版本模拟）"
n=0
while IFS= read -r f; do
  /usr/bin/python3 -m py_compile "$f" || { echo "  ✗ $f"; fail=1; }
  n=$((n+1))
done < <(find payload -name '*.py')
echo "  $n 个产品文件通过 3.9.6 编译"
find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

step "结果"
[ $fail -eq 0 ] && echo "  全绿 ✓" || echo "  有失败 ✗"
exit $fail
