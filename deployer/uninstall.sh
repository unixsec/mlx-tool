#!/bin/bash
# T0 · 卸载器。**卸载前必须先收敛服务**（裁决 R3-1）。
set -euo pipefail

CODE_ROOT="${MLX_ROOT_CODE:-$HOME/.local/share/mlx_server}"
# 数据根与权重根以 constants.env 为准（环境变量优先）：装在自定义根上的用户，
# 「保留：…」报出的必须是他真正的目录，而不是默认值。
for _k in MLX_ROOT_DATA MLX_ROOT_WEIGHTS; do
  _v=$(sed -n "s/^${_k}=//p" "$CODE_ROOT/share/constants.env" 2>/dev/null || true)
  if [ -n "$_v" ]; then eval ": \${$_k:=\$_v}"; fi
done
DATA_ROOT="${MLX_ROOT_DATA:-$HOME/.local/state/mlx_server}"
WEIGHTS_ROOT="${MLX_ROOT_WEIGHTS:-$HOME/.local/share/mlx_models}"
say() { printf '%s\n' "$*" >&2; }

# 合法出口只有三个：0 走完 · 5 用户取消 · 6 服务未收敛而拒绝卸载。
# 其余任何退出都意味着脚本半途夭折（set -e 最擅长制造这个），
# 而半途夭折的卸载**看起来和成功一样** —— 必须让它出声。
_done=0
trap 'st=$?; if [ "$_done" != 1 ] && [ "$st" != 5 ] && [ "$st" != 6 ]; then
        printf "!! 卸载没走完就退出了（rc=%s）——代码根未清理：%s\n" "$st" "$CODE_ROOT" >&2
        printf "   这是缺陷，不是正常结束。请重跑，或把这行报告出来。\n" >&2
      fi' EXIT

# ── 步 5 · 破坏性确认（委托 T1，与委托 vault 同一形态）──
if [ "${MLX_ASSUME_YES:-}" != "1" ]; then
  say "将删除：${CODE_ROOT}（代码根）"
  say "保留：  ${DATA_ROOT}（数据根）"
  say "       ${WEIGHTS_ROOT}（权重根）"
  printf '键入 yes 确认: ' >&2
  # **重定向失败发生在 shell 展开阶段**，`read` 自己的 `2>` 管不到它 ——
  # 无 tty 时 bash 会把 `/dev/tty: Device not configured` 原样打给用户。
  # 拒绝行为是对的，但那行 shell 内部报错只会让人以为出了别的毛病。
  # 花括号把重定向一起包进子作用域，`2>` 才盖得住。
  { read -r ans </dev/tty; } 2>/dev/null || ans=""      # EOF ⇒ 拒绝
  [ "$ans" = "yes" ] || { say "已取消，未删除任何东西"; exit 5; }
fi

# ── P7 · 服务收敛前置（在动任何盘之前、run/ 仍可读时）──
if [ -x "$CODE_ROOT/bin/mlx" ] && [ -d "$DATA_ROOT/run" ]; then
  say "== 收敛正在运行的服务"
  # stderr **不重定向**：这条命令的失败是「拒绝卸载」的唯一依据，
  # 吞掉它等于让用户和维护者都看不见拒绝的理由（排查时就因此丢过两小时）
  if ! "$CODE_ROOT/bin/mlx" down --all; then
    say "!! 以下服务未能停止，拒绝卸载（rc 6）——"
    say "   否则工具被删掉之后你将失去停止它们的手段："
    ls "$DATA_ROOT/run"/*.json 2>/dev/null | sed 's/.*\//   /;s/\.json$//' >&2 || true
    exit 6
  fi
  # `ls <glob> | wc -l` 在 set -euo pipefail 下是地雷：glob 无匹配时 ls 返回 2，
  # pipefail 把整条管道判负，赋值语句的 rc 就是它 ⇒ **脚本在这一行静默退出**，
  # 下面的删除步骤一行都不跑，而用户看到的是「收敛服务」之后的正常收尾。
  # find 在目录存在时即便无匹配也返回 0 —— 这里唯一安全的形态。
  left=$(find "$DATA_ROOT/run" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')
  [ "$left" = "0" ] || { say "!! 仍有 $left 份运行记录，拒绝卸载"; exit 6; }
fi

# ── 清 rc 标记块（R-DEPLOY-24）·**必须在删 constants.env 之前** ──
# 标记串与扫描面都从 constants.env 读**完整串**，本脚本不构造、不拼接
# （G-RES-15：写死字面量会让两个实例共存时互相误删）。
CONST="${CODE_ROOT}/share/constants.env"
if [ -f "$CONST" ]; then
  RC_BEGIN=$(sed -n 's/^MLX_RC_BEGIN=//p' "$CONST")
  RC_END=$(sed -n 's/^MLX_RC_END=//p' "$CONST")
  RC_FILES=$(sed -n 's/^MLX_RC_FILES=//p' "$CONST")
  if [ -n "$RC_BEGIN" ] && [ -n "$RC_END" ]; then
    say "== 清 shell rc 的标记块"
    for f in $RC_FILES; do
      [ -f "$f" ] || continue
      tmp=$(mktemp "${f%/*}/.mlxrc.XXXXXX") || continue
      # 预期剩余行数 = 原行数 − 标记块行数。**空产出可能是正确结果** ——
      # 用户的 rc 若只含本工具的标记块，全删后就该是空的；
      # 用 `[ -s ]` 判会把这个正常场景误判为失败（安装侧踩过同一个坑）。
      n_old=$(wc -l < "$f" | tr -d " ")
      n_blk=$(awk -v b="$RC_BEGIN" -v e="$RC_END" \
                '$0==b{s=1} s{n++; if($0==e) s=0} END{print n+0}' "$f")
      n_want=$((n_old - n_blk))
      # 与安装侧同一条规则：**全等比较**识别边界
      if awk -v b="$RC_BEGIN" -v e="$RC_END" '
            $0==b { skip=1 } skip && $0==e { skip=0; next }
            skip { next } { print }' "$f" > "$tmp" 2>/dev/null \
         && [ "$(wc -l < "$tmp" | tr -d " ")" = "$n_want" ]; then
        if mv -f "$tmp" "$f"; then say "   ✓ ${f}"; else rm -f "$tmp"; fi
      else
        rm -f "$tmp"; say "   ! ${f} 未改动（过滤失败）"
      fi
    done
  fi
fi

# ── 清字节码缓存 ──────────────────────────────────────────────
# `__pycache__` 是**代码根内的可再生内容**：不在分发清单里（它随解释器版本变），
# 但卸载时必须清掉，否则残留检查会报「非空」而用户无从下手。
# 本机 `/usr/bin/python3`(3.9.6) 把它重定向到 ~/Library/Caches 因而不落这里，
# 但换个解释器就会落 —— 不能依赖那个默认值。
if [ -d "$CODE_ROOT" ]; then
  find "$CODE_ROOT" -type f -name '*.pyc' -exec rm -f {} + 2>/dev/null || true
fi

# ── 清 venv ────────────────────────────────────────────────────
# venv 在代码根内、不在分发清单里（上万个文件，逐条登记不可行 ——
# R-DEPLOY-38 的「清单外允许集合」）。卸载时必须清掉，否则残留检查报非空。
# **递归删除委托 T1 的 vault**（T0 无递归删除，INV-08）；
# T1 不可用时逐文件删 + rmdir 循环兜底。
if [ -d "$CODE_ROOT/.venv" ]; then
  say "== 清 venv"
  find "$CODE_ROOT/.venv" -type f -exec rm -f {} + 2>/dev/null || true
  find "$CODE_ROOT/.venv" -type l -exec rm -f {} + 2>/dev/null || true
  # **目录也要删。** 只删文件会留下一整棵空目录树，而下面那个 rmdir 循环
  # 只跑 5 轮 —— venv 的层级比它深，于是残留检查报「非空」、卸载中途夭折。
  # `find -depth -type d -empty -delete` 自底向上，一次走完（T0 无递归删除，
  # 这是逐目录的 rmdir，不是 rm -r）。
  find "$CODE_ROOT/.venv" -depth -type d -empty -delete 2>/dev/null || true
  rmdir "$CODE_ROOT/.venv" 2>/dev/null || true
fi

# ── 按 manifest 反向逐文件删 + 自底向上 rmdir ──
say "== 按分发清单移除"
if [ -f "$CODE_ROOT/share/manifest.tsv" ]; then
  while IFS=$'\t' read -r path kind action; do
    case "$path" in \#*|"") continue ;; esac
    [ "$action" = "删除" ] || continue
    case "$path" in
      */)  # 目录条目：逐文件 rm -f 后交给下面的 rmdir 循环（T0 无递归删除，INV-08）
           if [ -d "$CODE_ROOT/$path" ]; then
             find "$CODE_ROOT/$path" -type f -exec rm -f {} + 2>/dev/null || true
           fi ;;
      *)   rm -f "$CODE_ROOT/$path" ;;
    esac
  done < "$CODE_ROOT/share/manifest.tsv"
fi
# 嵌套空目录一轮 rmdir 删不完（删掉 lib/mlx_server 之后 lib 才变空），循环到不动为止
for _ in 1 2 3 4 5; do
  before=$(find "$CODE_ROOT" -type d 2>/dev/null | wc -l || true)
  find "$CODE_ROOT" -depth -type d -empty -exec rmdir {} + 2>/dev/null || true
  after=$(find "$CODE_ROOT" -type d 2>/dev/null | wc -l || true)
  if [ "$before" = "$after" ]; then break; fi
done

if [ -d "$CODE_ROOT" ] && [ -n "$(ls -A "$CODE_ROOT" 2>/dev/null)" ]; then
  say "!! 代码根仍有残留（不强删，逐项列出）："
  find "$CODE_ROOT" -mindepth 1 | sed 's/^/   /' >&2
  exit 1
fi
rmdir "$CODE_ROOT" 2>/dev/null || true
_done=1
say "== 完成。以下两处保留："
_done_note() { say "   $1"; }
_done_note "${DATA_ROOT}（数据根）"
_done_note "${WEIGHTS_ROOT}（权重根）"
