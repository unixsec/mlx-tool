#!/bin/bash
# T0 · 安装器。宿主机侧，不进载荷。
# 八种动作闭集：建目录 · 拷文件 · 写常量 · 建 venv · 装依赖 · 子进程调 T1 · 挂 PATH · 校验
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
CODE_ROOT="${MLX_ROOT_CODE:-$HOME/.local/share/mlx_server}"
# **重装时继承既有 constants.env**（环境变量优先，它是本次调用的显式意图）。
# 不继承的后果：装在自定义 rc 面或自定义网关端口上的用户重装一次，
# `MLX_RC_FILES` 回落 `~/.zshrc`、`MLX_GATEWAY_PORT` 回落 8080 ——
# 旧 rc 文件里的标记块就此成为孤儿（`constants.env` 已改指新面，
# 卸载器扫不到它），「写入面 == 扫描面」当场破。
# 注意 `uninstall.sh` 一直是读 constants.env 的，两个部署器此前不对称。
_INHERIT="$CODE_ROOT/share/constants.env"
if [ -f "$_INHERIT" ]; then
  while IFS='=' read -r _k _v; do
    case "$_k" in
      # 数据根与权重根同理：权重根默认值在 1.1.0 改过（`~/lab/models/mlx` → `~/.local/share/mlx_models`），
      # 不继承的话老用户一重装就指向空目录，已下载的模型全部「找不到」。
      MLX_GATEWAY_PORT|MLX_PORT_RANGE|MLX_RC_FILES|MLX_INSTANCE_ID|MLX_ROOT_DATA|MLX_ROOT_WEIGHTS)
        eval ": \${$_k:=\$_v}" ;;
    esac
  done < "$_INHERIT"
fi
DATA_ROOT="${MLX_ROOT_DATA:-$HOME/.local/state/mlx_server}"
# 权重根默认与另外两根一样走 XDG 布局，且独立于代码根（卸载不波及）。
WEIGHTS_ROOT="${MLX_ROOT_WEIGHTS:-$HOME/.local/share/mlx_models}"
GATEWAY_PORT="${MLX_GATEWAY_PORT:-8080}"
INSTANCE_ID="${MLX_INSTANCE_ID:-mlx}"
# 标记串是 INSTANCE_ID 派生的**完整串**（G-RES-15）。
# 这里是全项目唯一的构造点；写进 constants.env 后，所有读者（uninstall.sh /
# ctx / ui）只读完整串，**禁止再拼接** —— 两个实例共存时前缀匹配会让它们
# 互相误删，那是数据丢失路径。
RC_BEGIN="# >>> mlx_server:${INSTANCE_ID} >>>"
RC_END="# <<< mlx_server:${INSTANCE_ID} <<<"
# 写入面 == 扫描面（R-DEPLOY-24）：本变量是唯一定义点，卸载器从 constants.env 读它
RC_FILES="${MLX_RC_FILES:-$HOME/.zshrc}"
PORT_RANGE="${MLX_PORT_RANGE:-8100-8199}"

say() { printf '%s\n' "$*" >&2; }   # 过程说明走 stderr（INV-17）

FORCE=0
for _a in "$@"; do
  case "$_a" in
    --force) FORCE=1 ;;
    -h|--help)
      say "用法：bash deployer/install.sh [--force]"
      say "  可反复运行：代码、配置、PATH 都没变且自检通过时直接跳过（不停服务）。"
      say "  --force  无论如何都重装"
      exit 0 ;;
    *) say "未知参数：${_a}（用法：bash deployer/install.sh [--force]）"; exit 2 ;;
  esac
done

# ── 平台：只支持 Apple Silicon ─────────────────────────────────────
# 推理后端 mlx 只发 arm64。判据用 `hw.optional.arm64` 而不是 `uname -m`：
# Rosetta 下运行的终端里 `uname -m` 报 x86_64，会把 M 系列误判成 Intel。
# 必须在动任何盘之前判（日志目录也不建）。`MLX_SYSCTL` 是测试注入面。
_ARM=$("${MLX_SYSCTL:-/usr/sbin/sysctl}" -n hw.optional.arm64 2>/dev/null || true)
if [ "$_ARM" != "1" ]; then
  say "!! 本工具只支持 Apple Silicon（M 系列芯片）的 Mac —— 推理后端 mlx 只发 arm64 版本。"
  say "   这台机器不是（hw.optional.arm64=${_ARM:-取不到}），未做任何改动。"
  exit 70
fi

VENV="$CODE_ROOT/.venv"
PATH_LINE="export PATH=\"$CODE_ROOT/bin:\$PATH\""
# constants.env 的完整内容。先算出来：「要不要重装」要拿它和已装的逐字节比。
CONSTANTS="MLX_ROOT_CODE=$CODE_ROOT
MLX_ROOT_DATA=$DATA_ROOT
MLX_ROOT_WEIGHTS=$WEIGHTS_ROOT
MLX_GATEWAY_PORT=$GATEWAY_PORT
MLX_PORT_RANGE=$PORT_RANGE
MLX_CMD_NAME=mlx
MLX_VENV=$CODE_ROOT/.venv
MLX_INSTANCE_ID=$INSTANCE_ID
MLX_RC_BEGIN=$RC_BEGIN
MLX_RC_END=$RC_END
MLX_RC_FILES=$RC_FILES"

# 屏幕上把 $HOME 显示成 `~`：好读，用户贴输出求助时也不带出用户名。日志里保留完整路径。
# 只替换「$HOME/」与行尾的「$HOME」，不碰 /Users/alexander 这类前缀相同的路径。
_tilde() {
  awk -v h="$HOME" '
    length(h) > 1 {
      out = ""; s = $0
      while ((i = index(s, h)) > 0) {
        nx = substr(s, i + length(h), 1)
        if (nx == "/" || nx == "") { out = out substr(s, 1, i - 1) "~"; s = substr(s, i + length(h)) }
        else { out = out substr(s, 1, i + length(h) - 1); s = substr(s, i + length(h)) }
      }
      $0 = out s
    }
    { print; fflush() }'
}

# 候选按**版本从高到低**试，最后才回落系统 python3。
# `MLX_VENV_PYTHON` 可显式指定（注入面，测试与特殊环境用）。
# **只认 Homebrew（与 /usr/local 上的第三方安装），不认 /usr/bin/python3**
# 〔BCR-2026-09-02，`00-inheritance` §0.4.2〕。系统解释器建的 venv 有两个问题：
#   ① 3.9 装不上 0.29.3 之后的 mlx（只发 cp310+）⇒ 新模型架构永远跑不了
#   ② venv 绑在系统解释器上，macOS 升级替换它时 venv 会坏
# 这里**刻意不把 `/usr/bin/python3` 放进候选**：即便将来 macOS 自带 3.12，
# 上面第 ② 条仍然成立 —— 那不是版本问题，是「谁来管这个解释器」的问题。
_pick_python() {
  local c
  for c in "${MLX_VENV_PYTHON:-}" \
           /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 \
           /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
           /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3 \
           /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    # 判据是**能不能装上新版 mlx**，即 minor ≥ 10 —— 不是「叫什么名字」
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

# 备份进数据根（卸载时保留 ⇒ 天然可回滚）。**不写 $HOME 根。**
# 与同名的最近一份备份逐字节相同就不再复制 —— 重装多少次，备份只随内容变化增加。
# `cp -p` 保留权限：settings.json 是 0600、里面有密钥，备份不能比原件更宽。
# 输出带上是哪一种：只给路径时调用方分不清，屏幕上「（备份 <几天前的时间戳>）」读起来像备份没做。
_backup() {                         # _backup <file> → stdout: 「new <路径>」或「same <路径>」
  local f="$1" name last
  name=$(basename "$f")
  mkdir -p "$DATA_ROOT/backups" || return 1
  # 只认「<名>.<时间戳>」：`<名>.*` 会把 `zshrc.fresh.<时间戳>` 也算进来，字典序还排在最后
  last=$({ ls -1d "$DATA_ROOT/backups/$name".[0-9]*-[0-9]* 2>/dev/null || true; } | tail -n 1)
  if [ -n "$last" ] && [ -f "$last" ] && cmp -s "$f" "$last"; then
    echo "same $last"; return 0
  fi
  last="$DATA_ROOT/backups/$name.$(date +%Y%m%d-%H%M%S)"
  cp -p "$f" "$last" || return 1
  echo "new $last"
}
_backup_said() {                    # _backup_said <_backup 的输出> → 给人看的半句
  case "$1" in
    same\ *) echo "内容与上次备份相同，沿用 ${1#same }" ;;
    *)       echo "已备份到 ${1#new }" ;;
  esac
}

# ── 已是当前版本？──────────────────────────────────────────────────
# 安装器可以反复跑：**代码逐文件相同 + constants.env 相同 + 每个 rc 里都有 PATH 行 + 自检通过**
# 才跳过。四项缺一就照常重装 —— 判据宁可多装一次，也不能漏掉一处该修的。
# 自升级（`mlx install`，源即代码根）比较必然相同，不判。
#
# **受管树与生成物只在这里定义一次**：「要不要重装」的比较与拷贝后的对齐（`_prune`）读同一份。
# 两边各写一份时，比较要求逐文件相同、拷贝却只增不删 —— 旧版装过、新版删掉的文件让比较永远不等
# （2026-10-28 真机：1.0.0 的 `payload/share/types.tsv`，每次重跑都停服务重装）。
# 「源目录:代码根目录」；生成物是安装器在 share/ 里写出来、源码树里没有的文件。
TREES="payload/bin:bin payload/lib:lib payload/libexec:libexec payload/gateway:gateway payload/share:share payload:payload deployer:deployer"
SHARE_GENERATED="constants.env caps.json"
_up_to_date() {
  local t f x gen=""
  [ "$FORCE" = 0 ] && [ "$SRC" != "$CODE_ROOT" ] && [ -x "$CODE_ROOT/bin/mlx" ] || return 1
  for f in $SHARE_GENERATED; do gen="$gen -x $f"; done
  for t in $TREES; do
    x=""; [ "${t#*:}" = share ] && x=$gen
    # shellcheck disable=SC2086  # $x 是有意分词的选项列表
    diff -rq -x .gitkeep -x __pycache__ $x "$SRC/${t%%:*}" "$CODE_ROOT/${t#*:}" >/dev/null 2>&1 || return 1
  done
  [ "$(cat "$CODE_ROOT/share/constants.env" 2>/dev/null)" = "$CONSTANTS" ] || return 1
  for f in $RC_FILES; do grep -qxF "$PATH_LINE" "$f" 2>/dev/null || return 1; done
  CHECK_OUT=$(MLX_SHARE_ROOT="$CODE_ROOT/share" "$CODE_ROOT/bin/mlx" check 2>&1) || return 1
}

_main() {
CHECK_OUT=""
if _up_to_date; then
  say "== 已是当前版本：代码、配置、PATH 都没变，自检通过 —— 跳过重装，正在运行的服务未动"
  say "   要强制重装：bash deployer/install.sh --force"
  ACTION="已是最新，未重装"; CHECK_OK=1; STOPPED=""; rc_ok=1
  _summary
  return 0
fi
ACTION="已安装"

# 前置提示：没有 ≥3.10 的 Python 时**开头就说**，而不是等装完、`mlx up` 起不来才发现。
# 不替用户 `brew install` —— 往别人机器上装软件要由他自己决定。
if ! _pick_python >/dev/null && \
   ! "$VENV/bin/python3" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
  say "!! 没找到 3.10 以上的 Python（Homebrew）。工具本体与下载照常可用，但新架构的模型跑不了。"
  say "   建议先装：brew install python@3.13，再重跑本安装器。现在继续安装……"
fi

# ── I0 · 重装前先收敛服务（裁决 R3-1）────────────────────────────
# 必须在动任何盘之前、且 run/ 仍可读时执行。
STOPPED=""
if [ -x "$CODE_ROOT/bin/mlx" ] && [ -d "$DATA_ROOT/run" ]; then
  # **先记下停的是谁**，装完要报出来。此前只停不记：重装一次之后服务就没了，
  # 而安装器最后一行只说「完成」—— 下一条 `mlx chat` 报「连不上」，
  # 没有任何线索指向这次重装。
  # 没有服务在跑时 `ls` 无匹配返回 1 —— 不兜住，pipefail + set -e 会让安装器在这里
  # 一行不打地 rc 1 退出（2026-10-26 真机重装踩到：`run/` 里只剩 `alias/`）。
  STOPPED=$({ ls "$DATA_ROOT/run"/*.json 2>/dev/null || true; } \
            | sed 's/.*\///;s/\.json$//' | tr '\n' ' ')
  STOPPED="${STOPPED% }"
  say "== 收敛正在运行的服务（重装前必须先停）"
  if ! "$CODE_ROOT/bin/mlx" down --all 2>/dev/null; then
    say "!! 有服务未能停止 —— 拒绝重装（rc 6）。先手工停掉它们："
    ls "$DATA_ROOT/run"/*.json 2>/dev/null | sed 's/.*\//   /;s/\.json$//' >&2 || true
    exit 6
  fi
fi

# ── 备份用户状态（服务已停，文件不会写到一半）──────────────────
# 安装器本身只「补空值」不覆盖，但 settings / 模型登记表一旦损坏就是用户的配置与模型全丢 —— 动盘前留一份。
say "== 备份状态文件"
_done=""
# 只备份**丢了找不回来**的：配置与模型登记表。caps.json 不备份 —— 它是派生数据
# （`mlx caps refresh` 随时重采），且每次安装都重采、带采集时间，备份它会让「内容没变不重复」失效。
for _p in "$DATA_ROOT/settings.json" "$DATA_ROOT/models.jsonl"; do
  [ -f "$_p" ] || continue
  _f=${_p##*/}
  if _b=$(_backup "$_p"); then _done="$_done $_f"; printf '%s → %s\n' "$_p" "${_b#* }" >> "${INSTALL_LOG:-/dev/null}"
  else say "!! ${_f} 备份失败 —— 拒绝继续（数据根不可写？）"; exit 73; fi
done
if [ -n "$_done" ]; then say "   ✓${_done} → $DATA_ROOT/backups/（内容没变的不重复备份）"
else say "   · 首次安装，没有要备份的"; fi

say "== 建三根"
mkdir -p "$CODE_ROOT" "$DATA_ROOT" "$WEIGHTS_ROOT"

say "== 拷载荷"
mkdir -p "$CODE_ROOT/bin" "$CODE_ROOT/lib" "$CODE_ROOT/libexec" "$CODE_ROOT/share" "$CODE_ROOT/gateway"
cp -R "$SRC/payload/bin/." "$CODE_ROOT/bin/"
cp -R "$SRC/payload/lib/." "$CODE_ROOT/lib/"
cp -R "$SRC/payload/libexec/." "$CODE_ROOT/libexec/"
cp -R "$SRC/payload/gateway/." "$CODE_ROOT/gateway/"
cp -R "$SRC/payload/share/." "$CODE_ROOT/share/"
# 部署器与载荷副本进代码根（R-DEPLOY-37）：
# · 没有 deployer/ ⇒ 源码树一删，用户手上只剩一堆无法收敛的文件
# · 没有 payload/  ⇒ `mlx install` 找不到重装的源（install.sh 用 $0/.. 定位载荷，
#   从代码根跑时那里必须有 payload/）
mkdir -p "$CODE_ROOT/deployer" "$CODE_ROOT/payload"
# **自升级时 SRC == CODE_ROOT**（`mlx install` 从代码根内的 deployer 跑）。
# 此时 `cp -R a/. a/` 是自己拷自己，BSD cp 报 "are identical" 退 1，
# 而 `set -e` 让脚本当场中止 —— 中止发生在 `mlx down --all` **已经停掉
# 全部服务之后**、载荷拷了一半时，留下「服务全停 + 半新半旧的代码根」。
if [ "$SRC" != "$CODE_ROOT" ]; then
  cp -R "$SRC/deployer/." "$CODE_ROOT/deployer/"
  cp -R "$SRC/payload/." "$CODE_ROOT/payload/"
else
  say "   · 源即目标（自升级），跳过 deployer/payload 自拷"
fi
# **退役文件**（spec §6.4）：`cp -R` 只增不删，旧版装过、本版删掉的文件会一直留着 —— 比较永远不等、
# 卸载器按**本版** manifest 删也扫不到它们。此前用手写的退役清单补，清单只写了 `share/types.tsv`，
# 漏了同一个文件在 `payload/` 下的副本。改为按源码对齐：受管树里源码没有的文件逐个删掉。
# 逐文件 `rm -f` + 只收源码里也没有的空目录，不递归删（INV-08）；路径都由 `$CODE_ROOT/<受管树>` 拼出。
_prune() {
  local src=$1 dst=$2 rel d k
  [ -d "$dst" ] || return 0
  ( cd "$dst" && find . -type f ! -name .gitkeep ) | while IFS= read -r rel; do
    rel=${rel#./}
    if [ "$dst" = "$CODE_ROOT/share" ]; then
      for k in $SHARE_GENERATED; do [ "$rel" = "$k" ] && continue 2; done
    fi
    case "/$rel" in            # 源码目录还在时，它的 __pycache__ 是 Python 自己的缓存，不碰
      */__pycache__/*) d=${rel%__pycache__/*}; [ -d "$src/$d" ] && continue ;;
    esac
    [ -e "$src/$rel" ] || rm -f "$dst/$rel"
  done
  ( cd "$dst" && find . -mindepth 1 -depth -type d ) | while IFS= read -r rel; do
    rel=${rel#./}
    case "/$rel" in */__pycache__) [ -d "$src/${rel%__pycache__}" ] && continue ;; esac
    [ -d "$src/$rel" ] || rmdir "$dst/$rel" 2>/dev/null || true
  done
}
for _t in $TREES; do _prune "$SRC/${_t%%:*}" "$CODE_ROOT/${_t#*:}"; done
chmod +x "$CODE_ROOT/deployer"/*.sh "$CODE_ROOT/payload/bin/mlx"
find "$CODE_ROOT" -name .gitkeep -delete
chmod +x "$CODE_ROOT/bin/mlx"

say "== 写 constants.env"
printf '%s\n' "$CONSTANTS" > "$CODE_ROOT/share/constants.env"

# ── 建 venv 并装推理依赖（R-DEPLOY-40）──────────────────────────
# **venv 的解释器要 ≥ 3.10**，否则推理后端被锁死在旧版本：
#   · `mlx 0.29.3` 是最后一个有 cp39 wheel 的版本
#   · `mlx 0.30.0` 起只发 cp310+（2026-10-31 查 PyPI：0.30.0 / 0.30.3 / 0.30.4 都没有 cp39）
#   · `mlx-lm ≥ 0.31.0`（新架构如 `qwen3_5` 所需）依赖 `mlx ≥ 0.30.4`
# ⇒ 用 3.9 建 venv，`pip install -U mlx-lm` 会以 ResolutionImpossible 失败，
#   而错误信息只说「依赖冲突」，一个字都不提解释器版本（实测踩过）。
#
# 此前这里写死 `/usr/bin/python3`，理由是「不引入 Homebrew 依赖」——
# 那个理由针对的是**工具本体**，而 venv 只服务下载与推理（§0.4.1/§0.4.2 的
# 区分）。T1 仍跑在 3.9.6 下限上，那部分一个字节都没变。
say "== 建 venv"
# 版本要求的**唯一说法**（各处提示都引用它，不各写一份）
MLX_PY_REQ="0.29.3 之后的 mlx 只发 cp310+，而 mlx-lm ≥ 0.31（qwen3_5 等新架构所需）依赖它：推理 venv 要 Python ≥ 3.10"


if [ -x "$VENV/bin/python3" ]; then
  _V=$("$VENV/bin/python3" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0)
  _OK=$("$VENV/bin/python3" -c 'import sys;print(1 if sys.version_info[:2]>=(3,10) else 0)' 2>/dev/null || echo 0)
  if [ "$_OK" = "1" ]; then
    say "   ✓ 已存在  $($VENV/bin/python3 -V 2>&1)"
  else
    # **就地升级**：3.9 的 venv 装不了新 mlx，留着它等于留一个装不了后端的环境。
    # 权重在权重根、注册表在数据根，重建 venv 一个都不动。
    say "   ! 已存在的 venv 是 Python $_V —— $MLX_PY_REQ"
    if _PY=$(_pick_python); then
      say "   · 用 $($_PY -V 2>&1) 重建（权重与注册表不受影响）"
      # **先建新的、成功了再换掉旧的**：此前先 rm -rf 旧 venv，新的建失败就一个都没有了
      rm -rf "$VENV.rebuild"
      if "$_PY" -m venv "$VENV.rebuild" && [ -x "$VENV.rebuild/bin/python3" ]; then
        rm -rf "$VENV"
        mv "$VENV.rebuild" "$VENV"
        say "   ✓ $VENV  $($VENV/bin/python3 -V 2>&1)"
      else
        rm -rf "$VENV.rebuild"
        say "   ! 新 venv 建立失败 —— 保留旧的 Python $_V venv，新模型架构跑不了"
      fi
    else
      say "   ! 找不到 ≥3.10 的解释器 —— 保留旧 venv，但新模型架构跑不了"
      say "     装一个：brew install python@3.13"
    fi
  fi
elif _PY=$(_pick_python); then
  say "   ✓ $VENV  $("$_PY" -V 2>&1)"
  "$_PY" -m venv "$VENV" 2>/dev/null || say "   ! venv 建立失败"
elif /usr/bin/python3 -m venv "$VENV" 2>/dev/null && [ -x "$VENV/bin/python3" ]; then
  # 没有 Homebrew Python ⇒ 仍用系统的建一个（`huggingface_hub` 在 3.9 上能装，
  # **下载功能可用**），但**当场说清推理受限**，而不是让用户等到
  # `mlx up` 失败、或 `pip install -U mlx-lm` 报一条不提解释器版本的
  # 依赖冲突时才发现（§0.4.2「明说的降级，不是静默失败」）。
  say "   ✓ $VENV  $($VENV/bin/python3 -V 2>&1)（macOS 自带）"
  say "   ! 没找到 Homebrew 的 Python —— 下载可用，但**新模型架构跑不了**"
  say "     $MLX_PY_REQ"
  say "     修：brew install python@3.13，然后重跑本安装器（会自动重建 venv）"
else
  say "   ! venv 建立失败 —— 工具仍可用（回落系统 python3），但下载与推理不可用"
fi

if [ -x "$VENV/bin/python3" ] && [ "${MLX_SKIP_DEPS:-}" != "1" ]; then
  say "== 装推理依赖（失败不影响工具本体）"
  # **依赖装配失败不得使安装失败**（R-DEPLOY-40）：工具骨架、注册表、网关
  # 都不依赖它们，把整次安装拖垮会让用户连诊断命令都没有。
  MISSING=""
  for pkg in huggingface_hub mlx mlx-lm mlx-vlm; do
    if "$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check "$pkg" 2>/dev/null; then
      say "   ✓ $pkg"
    else
      say "   ! $pkg 未装上"
      MISSING="$MISSING $pkg"
    fi
  done
  if [ -n "$MISSING" ]; then
    say "   这些包没装上：$MISSING"
    say "   影响：下载权重（需 huggingface_hub）、启动文本模型（需 mlx-lm）、启动多模态模型（需 mlx-vlm）不可用。"
    say "   补装：$VENV/bin/python3 -m pip install$MISSING"
  fi
elif [ "${MLX_SKIP_DEPS:-}" = "1" ]; then
  # 测试用：venv 照建（回落路径与解释器优先级都要测），但不花几分钟装大包。
  # 真装依赖的验证走单独一条用例，不拖垮每个 e2e 的 fixture。
  say "== 跳过推理依赖（MLX_SKIP_DEPS=1）"
fi

# 边 #3（`03b-ledgers.md:51`）：**子进程调 T1 采集能力，检查 rc，不是 exec。**
# 此前这一步不存在，于是全新安装之后用户的第一条 `mlx up` 必然 rc 6
# 「尚未采集后端能力信息」—— 装完就得先自己发现要跑一次 caps refresh。
# 采集失败**不中止安装**：venv 没建成时它必然失败，而工具本体仍然可用。
say "== 采集后端能力"
# 失败要**说出原因**：此前输出被丢进 /dev/null，用户只看到「采集失败」，
# 查不到是 venv 没建成、包没装上、还是别的。
if caps_out=$(MLX_SHARE_ROOT="$CODE_ROOT/share" "$CODE_ROOT/bin/mlx" caps refresh 2>&1); then
  say "   ✓ caps.json"
else
  say "   ! 采集失败：$(printf '%s' "$caps_out" | tail -n 3)"
  say "     装好推理依赖后手动跑：mlx caps refresh"
fi

say "== 生成密钥（只补空值，已有的不动）"
# spec §5.2：两把密钥（backend.api_key、api.token）存 settings.json，0600。
# `bootstrap keys` 的输出**只含键名**，所以原样转印是安全的。
if keys_out=$(MLX_SHARE_ROOT="$CODE_ROOT/share" "$CODE_ROOT/bin/mlx" bootstrap keys 2>&1); then
  printf '%s\n' "$keys_out" | sed 's/^/   /'
else
  say "   ! 密钥生成失败：$(printf '%s' "$keys_out" | tail -n 3)"
  say "     补齐之前，多模态模型（mlx_vlm）与网关将拒绝启动。补：mlx bootstrap keys"
fi

say "== 网关暴露面"
# spec §4.1 升级提示：1.1.0 起 `api.bind` 默认 0.0.0.0。沿用默认的人（settings 里没显式设 api.bind）
# 装完后网关对 Wi-Fi / 有线 / 虚拟机网段开放 —— 必须醒目说出来；显式设过的尊重其选择，不打扰。
if MLX_SETTINGS="$DATA_ROOT/settings.json" /usr/bin/python3 -c '
import json, os, sys
p = os.environ["MLX_SETTINGS"]
sys.exit(0 if os.path.exists(p) and "api.bind" in json.load(open(p)) else 1)' 2>/dev/null; then
  say "   · api.bind 已显式设置，保持不变"
else
  say "   !! 网关默认绑定全部网卡（0.0.0.0）：本机、Parallels 虚拟机、同一 Wi-Fi / 有线网段都能连。"
  say "      所有来源都要令牌（查看：mlx config get api.token --reveal）；VPN、雷雳网桥等接口一律拒绝。"
  say "      只给本机用：mlx config set api.bind --value=127.0.0.1"
fi

say "== 校验产物（按 manifest.tsv）"
missing=0
while IFS=$'\t' read -r path kind _; do
  case "$path" in \#*|"") continue ;; esac
  [ -e "$CODE_ROOT/$path" ] || { say "   缺失: $path"; missing=$((missing+1)); }
done < "$CODE_ROOT/share/manifest.tsv"
[ "$missing" -eq 0 ] || { say "!! 分发清单缺 $missing 项，安装未完成"; exit 71; }

# ── 挂 PATH（R-DEPLOY-24）──────────────────────────────────────

_rc_write() {                       # _rc_write <rc-file>
  local rc="$1" tmp bak nold nnew ndel want
  [ -e "$rc" ] || : > "$rc" 2>/dev/null || { say "   ! 无法创建 $rc"; return 1; }

  bak=$(_backup "$rc" 2>/dev/null) || { say "   ! 备份失败，$rc 保持不动"; return 1; }

  nold=$(wc -l < "$rc" | tr -d " ")
  # **预期删除的行数**，事先精确算出：本工具标记块的全部行 + 块外每一行
  # 全等于本工具 PATH 行的 + 用户手工加的旧注释头。
  # 过滤后必须**恰好**剩 nold - ndel 行 —— 用「下界」不够：下界只能拦住
  # 「删太多」，拦不住「删太少」；而且下界公式一旦漏算某一类该删的行
  # （本项目实测：漏算了接管行），正常安装会被自己误判成截断。
  ndel=$(awk -v b="$RC_BEGIN" -v e="$RC_END" -v p="$PATH_LINE" '
           $0==b { s=1 }
           s { n++; if ($0==e) s=0; next }
           $0==p || $0=="# === mlx tool ===" { n++ }
           END { print n+0 }' "$rc")

  want=$((nold - ndel))
  tmp=$(mktemp "${rc%/*}/.mlxrc.XXXXXX") || return 1
  # **全等比较（$0==b）识别块边界，不用前缀或正则** —— 用户的 rc 里有别的工具的
  # 标记块（实测本机有一个 53 行的）；前缀匹配会把 begin 命中它、end 命中不上，
  # 于是删掉两者之间的全部内容，而退出码是 0。
  # 接管：整行等于本工具 PATH 行的（用户手工加的）全部吸收。
  # **爆炸半径由「全等比较」保证，不由计数保证** —— 计数拦不住一行删错，
  # 却会在用户有重复行时制造不幂等。
  if ! awk -v b="$RC_BEGIN" -v e="$RC_END" -v p="$PATH_LINE" '
        $0==b { skip=1 }
        skip && $0==e { skip=0; next }
        skip { next }
        $0==p { next }                       # 接管：整行全等才删
        $0=="# === mlx tool ===" { next }
        { print }' "$rc" > "$tmp" 2>/dev/null; then
    rm -f "$tmp"; say "   ! rc 过滤失败，$rc 未改动（备份在 ${bak}）"; return 1
  fi

  # **三重验证后才替换**：过滤程序的 rc == 0（上面）· 产出非空 · 删除行数恰好等于预期。
  # 过滤程序被信号杀死时输出是全缓冲的，可能一字节未 flush ——
  # 无条件 mv 会把用户 86 行的 rc 变成 3 行，而函数还返回 0。
  # 判据是 `want`（预期剩余行数）不是 `nold`：用户的 rc 若**只含本工具的内容**，
  # 过滤后为空是**正确结果**，用 nold>0 会把这个正常场景误拒。
  if [ ! -s "$tmp" ] && [ "$want" -gt 0 ]; then
    rm -f "$tmp"; say "   ! rc 过滤产出为空（应剩 ${want} 行），${rc} 未改动（备份在 ${bak}）"; return 1
  fi
  nnew=$(wc -l < "$tmp" | tr -d " ")
  if [ "$nnew" != "$want" ]; then
    rm -f "$tmp"
    say "   ! rc 过滤删掉的行数不对（${nold} → ${nnew}，应为 ${want}），${rc} 未改动（备份在 ${bak#* }）"
    return 1
  fi

  printf '%s\n%s\n%s\n' "$RC_BEGIN" "$PATH_LINE" "$RC_END" >> "$tmp"
  mv -f "$tmp" "$rc" || { rm -f "$tmp"; return 1; }
  say "   ✓ ${rc}（$(_backup_said "$bak")）"
  return 0
}

say "== 挂 PATH"
rc_ok=0
for f in $RC_FILES; do
  if _rc_write "$f"; then rc_ok=1; fi
done

# ── 自检：装完当场跑一遍健康检查，不等用户自己想起来 ───────────
# 失败**不使安装失败**：依赖没装上（caps）这类降级前面已经明说过，
# 这里让它们以检查项的形式再出现一次，附带下一步。屏幕上只给结论与未过项，全文进日志。
say "== 自检"
if CHECK_OUT=$(MLX_SHARE_ROOT="$CODE_ROOT/share" "$CODE_ROOT/bin/mlx" check 2>&1); then
  CHECK_OK=1
else
  CHECK_OK=0
  printf '%s\n' "$CHECK_OUT" | grep -v '^[[:space:]]*✓' | grep -v '^[[:space:]]*$' >&2 || true
fi
_summary
}

# ── 结果清单：所有用户都看得懂的那几行 ─────────────────────────────
_summary() {
  local ver n first next bind keys hint=""
  ver=$(MLX_SHARE_ROOT="$CODE_ROOT/share" "$CODE_ROOT/bin/mlx" version 2>/dev/null)
  [ -z "$INSTALL_LOG" ] || printf '%s\n%s\n' "$ver" "$CHECK_OUT" >> "$INSTALL_LOG"
  ver=$(printf '%s\n' "$ver" | awk '/工具版本/ { print $2; exit }')
  # 模型数与第一个模型名、两把密钥是否齐、网关绑定 —— 只读文件，**不打印任何密钥值**。
  eval "$(MLX_DATA="$DATA_ROOT" /usr/bin/python3 - <<'PY' 2>/dev/null
import json, os, shlex
d = os.environ["MLX_DATA"]
ids = []
try:
    for ln in open(os.path.join(d, "models.jsonl")):
        ln = ln.strip()
        if ln:
            try:
                ids.append(json.loads(ln)["id"])
            except Exception:
                pass
except OSError:
    pass
try:
    s = json.load(open(os.path.join(d, "settings.json")))
except Exception:
    s = {}
miss = [k for k in ("api.token", "backend.api_key") if not s.get(k)]
print("n=%d first=%s keys=%s bind=%s" % (len(ids), shlex.quote(ids[0] if ids else ""),
      shlex.quote(" ".join(miss)), shlex.quote(str(s.get("api.bind", "0.0.0.0")))))
PY
)" || true
  say ""
  say "== 安装结果"
  say "  ✓ 版本      ${ver:-?}（${ACTION}）"
  if [ "${CHECK_OK:-0}" = 1 ]; then
    say "  ✓ 自检      $(printf '%s\n' "$CHECK_OUT" | awk '/项通过/ { $1 = $1; print; exit }')"
  else
    say "  ✗ 自检      有未通过的项（见上方，每项附下一步）；安装本身已完成"
  fi
  if [ -z "${keys-x}" ]; then say "  ✓ 密钥      已就绪（查看：mlx config get api.token --reveal）"
  else say "  ✗ 密钥      缺 ${keys:-?} —— 补：mlx bootstrap keys"; fi
  if [ "${bind:-}" = "127.0.0.1" ]; then
    say "  · 网关      http://127.0.0.1:${GATEWAY_PORT}/v1，只对本机（启动：mlx up api）"
  else
    say "  · 网关      http://127.0.0.1:${GATEWAY_PORT}/v1，也对局域网与虚拟机开放、均需令牌（启动：mlx up api）"
  fi
  say "  · 模型      已登记 ${n:-0} 个"
  _VV=$("$VENV/bin/python3" -c 'import sys;print(1 if sys.version_info[:2]>=(3,10) else 0)' 2>/dev/null || echo 0)
  if [ "$_VV" != "1" ]; then
    say "  ! 推理 venv  Python 低于 3.10，新模型架构跑不了 —— brew install python@3.13 后重跑安装器"
  fi
  if [ -n "$STOPPED" ]; then
    say "  ! 重装前停掉了：${STOPPED}（没有自动起回来）"
    next="mlx up $STOPPED"
  elif [ "${n:-0}" -gt 0 ]; then
    next="mlx up $first"
  else
    next="mlx model install <短名> --repo=<仓库>"
    hint="<仓库> 在 huggingface.co/mlx-community 挑，例如 mlx-community/Qwen3-0.6B-4bit"
  fi
  if [ "$rc_ok" != 1 ]; then
    say "  ✗ PATH      未写入 shell rc，手动加这一行：$PATH_LINE"
    say "  → 下一步：加好 PATH 后新开终端，$next"
  else
    case ":$PATH:" in
      *":$CODE_ROOT/bin:"*) say "  → 下一步：$next" ;;
      *) say "  → 下一步：新开一个终端（或 source ${RC_FILES%% *}），然后 $next" ;;
    esac
    [ -z "$hint" ] || say "            $hint"
  fi
  [ -z "$INSTALL_LOG" ] || say "  · 日志      $INSTALL_LOG"
}

# ── 入口 ───────────────────────────────────────────────────────────
# **整次安装留一份日志**（数据根，卸载时保留）。此前要靠调用者 `| tee`，忘了就没有记录。
# 只记 stderr：过程说明全在那里，密钥只打印键名（见「生成密钥」段）。
# 用管道而不是 `exec 2> >(tee …)`：bash 不等进程替换结束，交互终端里最后几行会
# 落到提示符之后；管道要等每一段都结束才返回。stdout 经 fd 3 原样旁路。
INSTALL_LOG="$DATA_ROOT/logs/install/$(date +%Y%m%d-%H%M%S).log"
mkdir -p "${INSTALL_LOG%/*}" 2>/dev/null && : >> "$INSTALL_LOG" 2>/dev/null || INSTALL_LOG=""
exec 3>&1
if [ -n "$INSTALL_LOG" ]; then
  _main 2>&1 1>&3 3>&- | tee -a "$INSTALL_LOG" | _tilde >&2
else
  _main 2>&1 1>&3 3>&- | _tilde >&2
fi
exit "${PIPESTATUS[0]}"
