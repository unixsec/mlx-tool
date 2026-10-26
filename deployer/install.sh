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
      MLX_GATEWAY_PORT|MLX_PORT_RANGE|MLX_RC_FILES|MLX_INSTANCE_ID)
        eval ": \${$_k:=\$_v}" ;;
    esac
  done < "$_INHERIT"
fi
DATA_ROOT="${MLX_ROOT_DATA:-$HOME/.local/state/mlx_server}"
WEIGHTS_ROOT="${MLX_ROOT_WEIGHTS:-$HOME/lab/models/mlx}"
GATEWAY_PORT="${MLX_GATEWAY_PORT:-8080}"
INSTANCE_ID="${MLX_INSTANCE_ID:-mlx}"
# 标记串是 INSTANCE_ID 派生的**完整串**（T5 第 12 行 · G-RES-15）。
# 这里是全项目唯一的构造点；写进 constants.env 后，所有读者（uninstall.sh /
# ctx / ui）只读完整串，**禁止再拼接** —— v6 与 v7 共存时前缀匹配会让它们
# 互相误删，那是数据丢失路径。
RC_BEGIN="# >>> mlx_server:${INSTANCE_ID} >>>"
RC_END="# <<< mlx_server:${INSTANCE_ID} <<<"
# 写入面 == 扫描面（R-DEPLOY-24）：本变量是唯一定义点，卸载器从 constants.env 读它
RC_FILES="${MLX_RC_FILES:-$HOME/.zshrc}"
PORT_RANGE="${MLX_PORT_RANGE:-8100-8199}"

say() { printf '%s\n' "$*" >&2; }   # 过程说明走 stderr（INV-17）

# ── I0 · 重装前先收敛服务（裁决 R3-1）────────────────────────────
# 必须在动任何盘之前、且 run/ 仍可读时执行。
STOPPED=""
if [ -x "$CODE_ROOT/bin/mlx" ] && [ -d "$DATA_ROOT/run" ]; then
  # **先记下停的是谁**，装完要报出来。此前只停不记：用户（和我自己）
  # 重装一次之后服务就没了，而安装器最后一行只说「完成」——
  # 下一条 `mlx chat` 报「连不上」，没有任何线索指向这次重装。
  # 2026-09-02 我自己踩了：验证通过后又部署一次，把 qwen38 停掉，
  # 然后告诉 Alex「服务没有问题」。
  # 没有服务在跑时 `ls` 无匹配返回 1 —— 不兜住，pipefail + set -e 会让安装器在这里
  # 一行不打地 rc 1 退出（2026-10-26 真机重装踩到：`run/` 里只剩 `alias/`）。
  STOPPED=$({ ls "$DATA_ROOT/run"/*.json 2>/dev/null || true; } \
            | sed 's/.*\///;s/\.json$//' | tr '\n' ' ')
  say "== 收敛正在运行的服务（重装前置）"
  if ! "$CODE_ROOT/bin/mlx" down --all 2>/dev/null; then
    say "!! 有服务未能停止 —— 拒绝重装（rc 6）。先手工停掉它们："
    ls "$DATA_ROOT/run"/*.json 2>/dev/null | sed 's/.*\//   /;s/\.json$//' >&2 || true
    exit 6
  fi
fi

say "== 建三根"
mkdir -p "$CODE_ROOT" "$DATA_ROOT" "$WEIGHTS_ROOT"

say "== 拷载荷"
mkdir -p "$CODE_ROOT/bin" "$CODE_ROOT/lib" "$CODE_ROOT/share" "$CODE_ROOT/gateway"
cp -R "$SRC/payload/bin/." "$CODE_ROOT/bin/"
cp -R "$SRC/payload/lib/." "$CODE_ROOT/lib/"
cp -R "$SRC/payload/gateway/." "$CODE_ROOT/gateway/"
cp -R "$SRC/payload/share/." "$CODE_ROOT/share/"
# **退役文件**（spec §6.4）：`cp -R` 只增不删，旧版装过的表会一直躺在 share/ 里，
# 而卸载器按**本版** manifest 删文件，永远扫不到它们。不进 manifest ——
# manifest 是本版产物清单，退役文件恰恰不是。
RETIRED=(share/types.tsv)
for _r in "${RETIRED[@]}"; do
  rm -f "$CODE_ROOT/$_r"
done
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
chmod +x "$CODE_ROOT/deployer"/*.sh "$CODE_ROOT/payload/bin/mlx"
find "$CODE_ROOT" -name .gitkeep -delete
chmod +x "$CODE_ROOT/bin/mlx"

say "== 写 constants.env（三根的兜底来源，裁决 R4-3 断口②）"
cat > "$CODE_ROOT/share/constants.env" <<EOF
MLX_ROOT_CODE=$CODE_ROOT
MLX_ROOT_DATA=$DATA_ROOT
MLX_ROOT_WEIGHTS=$WEIGHTS_ROOT
MLX_GATEWAY_PORT=$GATEWAY_PORT
MLX_PORT_RANGE=$PORT_RANGE
MLX_CMD_NAME=mlx
MLX_VENV=$CODE_ROOT/.venv
MLX_INSTANCE_ID=$INSTANCE_ID
MLX_RC_BEGIN=$RC_BEGIN
MLX_RC_END=$RC_END
MLX_RC_FILES=$RC_FILES
EOF

# ── 建 venv 并装推理依赖（R-DEPLOY-40）──────────────────────────
# **venv 的解释器要 ≥ 3.10**，否则推理后端被锁死在旧版本：
#   · `mlx 0.29.3` 是最后一个有 cp39 wheel 的版本
#   · `mlx 0.30.4` 起只发 cp310+
#   · `mlx-lm ≥ 0.31.0`（新架构如 `qwen3_5` 所需）依赖 `mlx ≥ 0.30.4`
# ⇒ 用 3.9 建 venv，`pip install -U mlx-lm` 会以 ResolutionImpossible 失败，
#   而错误信息只说「依赖冲突」，一个字都不提解释器版本（实测踩过）。
#
# 此前这里写死 `/usr/bin/python3`，理由是「不引入 Homebrew 依赖」——
# 那个理由针对的是**工具本体**，而 venv 只服务下载与推理（§0.4.1/§0.4.2 的
# 区分）。T1 仍跑在 3.9.6 下限上，那部分一个字节都没变。
say "== 建 venv"
VENV="$CODE_ROOT/.venv"

# 候选按**版本从高到低**试，最后才回落系统 python3。
# `MLX_VENV_PYTHON` 可显式指定（注入面，测试与特殊环境用）。
# **只认 Homebrew（与 /usr/local 上的第三方安装），不认 /usr/bin/python3**
# 〔BCR-2026-09-02，`00-inheritance` §0.4.2〕。系统解释器建的 venv 有两个问题：
#   ① 3.9 装不上 `mlx ≥ 0.30`（只发 cp310+）⇒ 新模型架构永远跑不了
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

if [ -x "$VENV/bin/python3" ]; then
  _V=$("$VENV/bin/python3" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0)
  _OK=$("$VENV/bin/python3" -c 'import sys;print(1 if sys.version_info[:2]>=(3,10) else 0)' 2>/dev/null || echo 0)
  if [ "$_OK" = "1" ]; then
    say "   ✓ 已存在  $($VENV/bin/python3 -V 2>&1)"
  else
    # **就地升级**：3.9 的 venv 装不了新 mlx，留着它等于留一个装不了后端的环境。
    # 权重在权重根、注册表在数据根，重建 venv 一个都不动。
    say "   ! 已存在的 venv 是 Python $_V —— 装不了 mlx ≥ 0.30（只发 cp310+）"
    if _PY=$(_pick_python); then
      say "   · 用 $($_PY -V 2>&1) 重建（旧 venv 移除，权重与注册表不受影响）"
      rm -rf "$VENV"
      "$_PY" -m venv "$VENV" && say "   ✓ $VENV  $($VENV/bin/python3 -V 2>&1)"
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
  say "     mlx ≥ 0.30 只发 cp310+，而 mlx-lm ≥ 0.31（qwen3_5 等所需）依赖它"
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
PATH_LINE="export PATH=\"$CODE_ROOT/bin:\$PATH\""

_rc_write() {                       # _rc_write <rc-file>
  local rc="$1" tmp bak nold nnew ndel want
  [ -e "$rc" ] || : > "$rc" 2>/dev/null || { say "   ! 无法创建 $rc"; return 1; }

  # 备份进数据根（卸载时保留 ⇒ 天然可回滚）。**不写 $HOME 根。**
  mkdir -p "$DATA_ROOT/backups"
  bak="$DATA_ROOT/backups/$(basename "$rc").$(date +%Y%m%d-%H%M%S)"
  cp "$rc" "$bak" 2>/dev/null || { say "   ! 备份失败，$rc 保持不动"; return 1; }

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
    say "   ! rc 过滤删掉的行数不对（${nold} → ${nnew}，应为 ${want}），${rc} 未改动（备份在 ${bak}）"
    return 1
  fi

  printf '%s\n%s\n%s\n' "$RC_BEGIN" "$PATH_LINE" "$RC_END" >> "$tmp"
  mv -f "$tmp" "$rc" || { rm -f "$tmp"; return 1; }
  say "   ✓ ${rc}（备份 ${bak}）"
  return 0
}

say "== 挂 PATH"
rc_ok=0
for f in $RC_FILES; do
  if _rc_write "$f"; then rc_ok=1; fi
done
if [ -n "$STOPPED" ]; then
  say "== 重装前停掉了这些服务，**没有自动起回来**："
  for s in $STOPPED; do say "     mlx up $s"; done
fi
if [ "$rc_ok" = "1" ]; then
  say "== 完成。**新开一个终端**，或先跑：source $RC_FILES"
else
  say "== 完成，但 PATH 未写入。手动加进 shell rc："
  say "   $PATH_LINE"
fi
