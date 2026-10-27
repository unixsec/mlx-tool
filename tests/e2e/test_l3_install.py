"""L3 · 跑**真实安装器 + 真实安装产物**。

v6 的教训（`c637647` 逐字）：L0/L1/L2 共 665 条断言全部针对仓库源码 + 合成夹具，
而用户运行的是**安装产物** —— 每一个用户可见故障都长在两者之间的缝里。
本文件的每条断言都跑在 `install.sh` 装出来的树上，不是源码树。
"""
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture()
def installed(tmp_path, repo):
    """跑真实安装器，返回安装根。"""
    roots = {k: tmp_path / v for k, v in
             (("MLX_ROOT_CODE", "code"), ("MLX_ROOT_DATA", "data"),
              ("MLX_ROOT_WEIGHTS", "weights"))}
    env = dict(os.environ, PATH="/usr/bin:/bin",
               **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, f"安装器失败：{r.stderr}"
    # 模型要**登记且权重在盘**：`up --dry-run` 走完整个校验段才分叉。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tv.sandbox import seed_model
    seed_model(roots["MLX_ROOT_DATA"], roots["MLX_ROOT_WEIGHTS"])
    return roots["MLX_ROOT_CODE"], roots["MLX_ROOT_DATA"], env


def _mlx(code_root, env, *argv):
    return subprocess.run([str(code_root / "bin" / "mlx"), *argv],
                          capture_output=True, text=True, timeout=25, check=False,
                          env=dict(env, MLX_SHARE_ROOT=str(code_root / "share")))


def test_l3_manifest_products_all_exist(installed):
    """`L3-01`：按 `manifest.tsv` 校验产物 —— **安装器本身是被测对象**。

    v6 的 `install.sh` 只拷 `*.sh/*.awk`，装出来的 lib/ 少一个 .py，
    而 665 条断言全绿 —— 因为它们跑的是源码树。
    """
    code_root, _, _ = installed
    declared, missing = [], []
    for line in (code_root / "share" / "manifest.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        path = line.split("\t")[0]
        declared.append(path)
        if not (code_root / path).exists():
            missing.append(path)
    Cardinality.require_nonempty("L3-01·manifest", declared, "清单为空 ⇒ 本条空真")
    Cardinality.check("L3-01·missing", missing, rel="empty")


def test_l3_dry_run_on_installed_product(installed):
    """装出来的东西**真的能跑** —— 而不只是文件存在。"""
    code_root, _, env = installed
    r = _mlx(code_root, env, "up", "qwen", "--dry-run")
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
    argv = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("[")][-1])
    # 真实安装产物里 venv 是建好的 ⇒ argv[0] 必须是**存在的可执行文件**。
    # 这是这条判据最强的形态：`--dry-run` 打印的东西照抄就能跑。
    assert argv[0] != "mlx_lm", f"argv[0] 仍是包名，不可执行：{argv}"
    assert os.path.isabs(argv[0]) and os.access(argv[0], os.X_OK), \
        f"argv[0] 不是可执行的绝对路径：{argv[0]}"
    assert argv[1:3] == ["-m", "mlx_lm.server"], argv
    assert "--model" in argv and "qwen" in argv


def test_l3_shim_interpreter_resolution_in_real_product(installed):
    """垫片在**安装产物**里：优先 venv、回落系统 python3、不走 PATH。

    〔BCR-2026-08-31 改判据〕变更前这里断言「仍写死系统解释器」——
    那是变更前的决策。现在的判据是三条同时成立：
      · 有 venv 时用 venv（否则装 `hf` / `mlx_lm` 没有意义）
      · **回落存在**（§0.4.1 的论证依赖它）
      · 不走 PATH（走 PATH 会让解释器随 brew 升级漂移）

    这是在**真实安装产物**上验的，不是源码树里的那份。
    """
    import re
    code_root, _, _ = installed
    shim = (code_root / "bin" / "mlx").read_text()
    assert ".venv/bin/python3" in shim, "产物里的垫片没有优先取 venv"
    assert "/usr/bin/python3" in shim, "产物里的垫片没有回落"
    code = [ln for ln in shim.splitlines() if not ln.lstrip().startswith("#")]
    bare = [ln.strip() for ln in code
            if re.search(r'(?<![/\w."$])python3\b(?![\w"/])', ln)]
    assert not bare, f"走 PATH 的裸调用：{bare}"

def test_l3_uninstall_refuses_when_service_cannot_be_stopped(installed):
    """**裁决 R3-1**：服务**未能停止**时卸载拒绝且零删除（rc 6）。

    注意裁决的措辞是「服务未能全部停止时」，不是「有服务在跑时」——
    卸载器的正路是先 `bin/mlx down --all` 自动收敛，收敛成功就继续卸载。
    本用例此前断言「在跑 ⇒ rc 6」，读错了裁决；而它一直绿是因为当时
    `down --all` 这个形态产品根本不支持 ⇒ 那次调用必然失败 ⇒ 必然 rc 6。
    **测试把缺陷固化成了期望。** 主路径（在跑 → 自动停 → 卸载成功）
    由 test_l3_uninstall_after_running_service 覆盖。

    构造真正会被否决的形态需要**两个**进程，这一点容易读错（我读错过一次）：
    「活着但端口没 bind」**不是**否决场景 —— 那是就绪超时的最常见形态，
    设计要求它**必须能被终止**（`may_kill` 的注释，E-7 逐字复现），
    所以拿一个不占端口的 sleep 当靶子只会看着它被正常杀掉。
    真正的否决场景是 **pid 被复用**：端口上有人监听，但那个监听者
    既不是记录的 pid、其 pgid 也不是记录的 pgid ⇒ `port_owner == Ok(False)`
    ⇒ 否决 ⇒ 不动手 ⇒ 记录留存 ⇒ down 失败 ⇒ 卸载据此拒绝。
    """
    code_root, data_root, env = installed
    # 靶子必须是**无辜的子进程**，绝不能是 os.getpid()：那是把测试进程本身
    # 交给被测代码去杀 —— 一旦否决项失效，pytest 当场被自己的用例杀死
    # （实测：exit 144）。用 sleep 还有个好处：它没被杀可以反过来作为
    # 「否决确实生效」的正面证据，而不只是「卸载拒绝了」这一个侧面。
    victim = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
    # 一个与靶子毫无关系的真监听者：pid 不同、pgid 也不同（新会话），
    # 于是 port_owner 能给出确定的 Ok(False) 而不是 Unknown
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import socket,time\n"
         "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
         "s.bind(('127.0.0.1',65500)); s.listen(1); time.sleep(300)"],
        start_new_session=True)
    try:
        time.sleep(0.6)                      # 等它真的 bind 上
        assert holder.poll() is None, "监听者没起来，用例无法构造"
        lstart = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(victim.pid)],
                                capture_output=True, text=True, check=False).stdout.strip()
        assert lstart, "取不到靶子的 lstart，用例无法构造"
        (data_root / "run").mkdir(parents=True, exist_ok=True)
        (data_root / "run" / "qwen.json").write_text(json.dumps(
            {"pid": victim.pid, "pgid": os.getpgid(victim.pid), "port": 65500,
             "started_at": lstart}))

        before = sorted(p.name for p in code_root.rglob("*"))
        r = subprocess.run(["bash", str(Path(__file__).resolve().parents[2] /
                                        "deployer" / "uninstall.sh")],
                           capture_output=True, text=True, timeout=30, check=False,
                           env=dict(env, MLX_ASSUME_YES="1"))
        after = sorted(p.name for p in code_root.rglob("*"))

        assert r.returncode == 6, f"服务停不掉时卸载应 rc 6，实得 {r.returncode}\n{r.stderr}"
        assert before == after, "拒绝卸载时仍删了东西"
        assert "失去停止它们的手段" in r.stderr or "未能停止" in r.stderr
        assert "按分发清单移除" not in r.stderr, "拒绝路径不应进入删除步骤"
        assert victim.poll() is None, (
            "靶子被杀了 —— 端口上明明是别人在监听，port_owner 的否决没生效，"
            "pid 复用时会误杀无辜进程")
    finally:
        for pr in (victim, holder):
            pr.kill()
            pr.wait(timeout=5)


def test_l3_uninstall_removes_declared_products(installed):
    """无服务在跑时，卸载按清单移除，**残留集合为空**（`L3-02`）。"""
    code_root, _, env = installed
    r = subprocess.run(["bash", str(Path(__file__).resolve().parents[2] /
                                    "deployer" / "uninstall.sh")],
                       capture_output=True, text=True, timeout=30, check=False,
                       env=dict(env, MLX_ASSUME_YES="1"))
    assert r.returncode == 0, f"卸载失败：{r.stderr}"
    leftover = list(code_root.rglob("*")) if code_root.exists() else []
    Cardinality.check("L3-02·残留", [str(p) for p in leftover], rel="empty")


def test_l3_install_surface_subset_of_manifest(installed):
    """**`G-DEPLOY-01`**：安装实际写入的每个文件都必须在 `manifest.tsv` 里。

    坏样本（v6 的原形态、本次也当场复现）：安装器拷了 gateway/ 五个文件
    而清单里没有 ⇒ 卸载扫不到它们 ⇒ **残留**。
    「安装写入面 ⊋ 卸载扫描面」正是这条守卫要挡的。
    """
    code_root, _, _ = installed
    declared = {ln.split("\t")[0] for ln in
                (code_root / "share" / "manifest.tsv").read_text().splitlines()
                if ln.strip() and not ln.startswith("#")}
    actual = {str(p.relative_to(code_root)) for p in code_root.rglob("*") if p.is_file()}
    Cardinality.require_nonempty("G-DEPLOY-01·actual", actual, "安装什么都没写 ⇒ 空真")

    # 目录条目（以 / 结尾）覆盖整棵子树。用于 `payload/` 这类**副本**：
    # 逐文件登记会与它所复制的那些条目形成两份事实，改一处漏一处。
    dirs = {d for d in declared if d.endswith("/")}
    # **清单外允许集合**（R-DEPLOY-38）：字节码缓存随解释器版本变，
    # 不该逐条登记；但卸载器必须能清掉它（uninstall.sh 已补）。
    unlisted = sorted(a for a in actual - declared
                      if not any(a.startswith(d) for d in dirs)
                      and "__pycache__" not in a and not a.endswith(".pyc")
                      # venv 是 R-DEPLOY-38 明写的「清单外允许集合」成员：
                      # 它有上万个文件，逐条登记不可行
                      and not a.startswith(".venv/"))
    Cardinality.check("G-DEPLOY-01·未登记的写入", unlisted, rel="empty")


def test_l3_uninstall_confirm_path_expands_variables(installed):
    """**不设 `MLX_ASSUME_YES` 的交互确认路径** —— 此前零测试覆盖。

    坏样本（本轮真实发生）：确认提示写作 `say "将删除：$CODE_ROOT（代码根）"`，
    而 bash 在这里会把全角括号的首字节并进变量名 ⇒ `set -u` 下
    `CODE_ROOT\\xef: unbound variable` ⇒ 用户**手动**卸载时第一行就崩。
    这三行只在 `MLX_ASSUME_YES != 1` 时执行，而所有既有测试都设了它
    ⇒ 「测试环境比真实环境富裕」的又一实例：用户唯一会走的路径没人走过。

    错误实现能否通过：`$VAR（` 写法在本用例下 stderr 出现 unbound variable ⇒ 红。
    """
    code_root, data_root, env = installed
    r = subprocess.run(["bash", str(Path(__file__).resolve().parents[2] /
                                    "deployer" / "uninstall.sh")],
                       capture_output=True, text=True, timeout=30, check=False,
                       stdin=subprocess.DEVNULL,
                       env={k: v for k, v in env.items() if k != "MLX_ASSUME_YES"})

    assert "unbound variable" not in r.stderr, (
        f"确认路径上有未展开的变量：{r.stderr}")
    assert "Traceback" not in r.stderr
    # 三个根的绝对路径都要出现在确认提示里 —— 用户据此判断删的是不是他想删的那套
    for root, name in [(code_root, "代码根"), (data_root, "数据根")]:
        assert str(root) in r.stderr, f"确认提示没有指名{name}的绝对路径：{r.stderr}"
    assert r.returncode == 5, f"无 tty 时应判为用户取消（rc 5），实得 {r.returncode}"
    assert (code_root / "bin" / "mlx").exists(), "取消时不得删除任何东西"


def test_l3_venv_created_and_interpreter_prefers_it(installed):
    """**venv 建立 + 解释器优先级**（R-DEPLOY-40）。

    变更理由见 `00-inheritance` §0.4.2：venv 装得进 `huggingface_hub` 与 `mlx_lm`，
    所以下载与推理才可能可用。**注意它不是为了"防止污染系统 python"** ——
    实测系统 python 的三个 site-packages 全部不可写（SIP），那个风险物理上不存在。
    """
    code_root, _, env = installed
    venv_py = code_root / ".venv" / "bin" / "python3"
    assert venv_py.exists(), "安装器没建 venv"

    r = subprocess.run([str(code_root / "bin" / "mlx"), "version", "--json"],
                       capture_output=True, text=True, timeout=30, check=False,
                       env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")})
    assert r.returncode == 0, r.stderr
    used = json.loads(r.stdout)["python"]
    assert ".venv" in used, f"有 venv 却没用它：{used}"


def test_l3_falls_back_to_system_python_when_venv_gone(installed):
    """**venv 没了，只读命令仍须可用** —— 这是 §0.4.1 论证的判据。

    §0.4.1 说「CLI 与推理 venv 的健康状况完全解耦，所以 venv 坏了 `mlx check`
    照样能诊断」。若垫片只认 venv，venv 目录一没它连启动都做不到，
    那条论证当场失效 —— 而那正是本项目选择「T1 仅标准库」的全部理由。

    错误实现能否通过：垫片写死 venv 路径 ⇒ 这里全部命令 rc 127。
    """
    code_root, _, env = installed
    venv = code_root / ".venv"
    assert venv.exists()
    moved = code_root / ".venv-moved"
    venv.rename(moved)
    try:
        base = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}
        for argv in (["version"], ["status"], ["model", "ls"], ["help"]):
            r = subprocess.run([str(code_root / "bin" / "mlx"), *argv],
                               capture_output=True, text=True, timeout=30,
                               check=False, env=base)
            assert r.returncode == 0, (
                f"venv 没了之后 `mlx {' '.join(argv)}` 挂了（rc={r.returncode}）—— "
                f"回落路径失效，§0.4.1 的论证随之失效\n{r.stderr}")
        # 且确实回落到了系统解释器
        r = subprocess.run([str(code_root / "bin" / "mlx"), "version", "--json"],
                           capture_output=True, text=True, timeout=30,
                           check=False, env=base)
        assert ".venv" not in json.loads(r.stdout)["python"], "没真的回落"
    finally:
        moved.rename(venv)


def test_l3_reinstall_removes_retired_types_table(tmp_path, repo):
    """spec §6.4：`cp -R` 只增不删 —— 1.0.0 留下的 `types.tsv` 要由安装器显式删掉。"""
    roots = {k: tmp_path / v for k, v in
             (("MLX_ROOT_CODE", "code"), ("MLX_ROOT_DATA", "data"),
              ("MLX_ROOT_WEIGHTS", "weights"))}
    (roots["MLX_ROOT_CODE"] / "share").mkdir(parents=True)
    old = roots["MLX_ROOT_CODE"] / "share" / "types.tsv"
    old.write_text("text\tmlx_lm\t--model\n")
    env = dict(os.environ, PATH="/usr/bin:/bin", **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, f"安装器失败：{r.stderr}"
    assert not old.exists(), "退役文件在重装后仍然存在"


def test_l3_reinstall_when_nothing_running(installed, repo):
    """真机形状：用过一阵、此刻没有服务在跑 —— `run/` 在，里面只有 `alias/` 子目录、没有 `*.json`。
    重装前的「记下停了谁」那条管道里 `ls` 无匹配返回 1，`pipefail` + `set -e` 曾让安装器
    一行不打、rc 1 静默退出（2026-10-26 真机重装踩到）。"""
    code_root, data_root, env = installed
    (data_root / "run" / "alias" / "qwen").mkdir(parents=True, exist_ok=True)
    assert not list((data_root / "run").glob("*.json"))
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh"), "--force"],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, f"空闲时重装失败 rc={r.returncode}：{r.stderr}"
    assert "收敛正在运行的服务" in r.stderr and "== 挂 PATH" in r.stderr


def test_l3_reinstall_backs_up_state_only_when_changed(installed, repo):
    """重装前把 settings / 模型登记表 / caps 备份进 `backups/`；**内容没变就不再加一份**。

    一键安装要能反复跑：每跑一次多三份一模一样的备份，几次之后真正有用的那份就找不到了。
    settings 里有密钥 ⇒ 备份不得比原件更宽（0600）。
    """
    code_root, data_root, env = installed
    backups = data_root / "backups"

    def reinstall():
        r = subprocess.run(["bash", str(repo / "deployer" / "install.sh"), "--force"],
                           capture_output=True, text=True, timeout=60, check=False, env=env)
        assert r.returncode == 0, f"重装失败：{r.stderr}"
        return {p.name for p in backups.iterdir()}

    originals = {n: (data_root / n).read_bytes() for n in ("settings.json", "models.jsonl")}
    first = reinstall()
    for name, data in originals.items():
        got = [backups / b for b in first if b.startswith(name + ".")]
        assert len(got) == 1, f"{name} 应恰好一份备份：{sorted(first)}"
        assert got[0].read_bytes() == data, f"{name} 的备份与重装前内容不同"
    st = [backups / b for b in first if b.startswith("settings.json.")][0]
    assert stat.S_IMODE(st.stat().st_mode) == 0o600, "settings 备份权限比原件宽"

    assert reinstall() == first, "内容没变，重装却又多了备份"

    s = json.loads((data_root / "settings.json").read_text())
    s["max_concurrency"] = 3
    (data_root / "settings.json").write_text(json.dumps(s))
    third = reinstall()
    added = third - first
    assert len(added) == 1 and next(iter(added)).startswith("settings.json."), sorted(added)


def test_l3_install_writes_own_log_and_self_checks(installed, repo):
    """安装器**自己**留日志、装完当场自检 —— 不靠调用者记得 `| tee`、记得跑 `mlx check`。"""
    code_root, data_root, env = installed
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh"), "--force"],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, r.stderr
    assert "== 自检" in r.stderr and "12 项" in r.stderr, r.stderr
    m = re.search(r"· 日志 +(\S+)", r.stderr)
    assert m, f"没报日志路径：{r.stderr[-500:]}"
    log = Path(m.group(1))
    assert log.parent == data_root / "logs" / "install", log
    text = log.read_text()
    for marker in ("== 备份状态文件", "== 建三根", "== 挂 PATH", "== 自检", "工具版本", "settings_mode"):
        assert marker in text, f"日志缺 {marker!r}"


def test_l3_fresh_install_generates_keys_without_printing_them(tmp_path, repo):
    """spec §6.4 第 4 步与 §5.2：全新安装生成两把密钥、0600、**值不出现在安装器输出里**。"""
    roots = {k: tmp_path / v for k, v in
             (("MLX_ROOT_CODE", "code"), ("MLX_ROOT_DATA", "data"),
              ("MLX_ROOT_WEIGHTS", "weights"))}
    env = dict(os.environ, PATH="/usr/bin:/bin", **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, f"安装器失败：{r.stderr}"
    st = roots["MLX_ROOT_DATA"] / "settings.json"
    assert st.exists(), f"安装器没有生成 settings.json：\n{r.stdout}"
    assert stat.S_IMODE(st.stat().st_mode) == 0o600
    s = json.loads(st.read_text())
    out = r.stdout + r.stderr
    for k in ("backend.api_key", "api.token"):
        v = s.get(k) or ""
        assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", v), f"{k} 没有生成或格式不对"
        assert v not in out and v[:8] not in out, f"安装器输出里出现了 {k} 的值"
        assert k in out, f"安装器输出应列出生成了哪些键（只列键名）：缺 {k}"


def test_l3_route_follows_config_on_installed_product(installed):
    """spec §8.1「D · 生效配置」：真 `bin/mlx`、真安装产物，配置改动真的改变判定与 argv。"""
    code_root, data_root, env = installed

    def dry():
        r = _mlx(code_root, env, "up", "qwen", "--dry-run")
        line = [ln for ln in r.stdout.splitlines() if ln.startswith("[")]
        return r.returncode, (json.loads(line[-1]) if line else None), r.stderr

    r = _mlx(code_root, env, "config", "set", "qwen", "--key=modality", "--value=vison")
    assert r.returncode == 6, f"modality 值域拼错应 rc 6：{r.stderr}"

    assert _mlx(code_root, env, "config", "set", "qwen", "--key=modality",
                "--value=vision").returncode == 0
    rc, _, err = dry()
    assert rc == 6 and "mlx_vlm" in err, f"venv 没装 mlx_vlm 时强制 vision 应拒绝（P1）：{err}"

    assert _mlx(code_root, env, "config", "set", "qwen", "--key=modality",
                "--value=text").returncode == 0
    assert _mlx(code_root, env, "config", "set", "max_concurrency", "--value=2").returncode == 0
    settings = data_root / "settings.json"
    s = json.loads(settings.read_text())
    s["backend"] = "mlx_lm"                     # 1.0.0 留下的退役键
    settings.write_text(json.dumps(s))
    rc, argv, err = dry()
    assert rc == 0, err
    assert argv[1:3] == ["-m", "mlx_lm.server"], argv
    assert argv[-4:] == ["--decode-concurrency", "2", "--prompt-concurrency", "2"], argv
    assert "判定  R4" in err and "已退役的键：backend" in err, err
    assert "Traceback" not in err


def test_l3_keys_never_leak_into_files_or_output(installed):
    """spec §8.3「密钥 · 泄漏」的计划 1 子集：跑遍会碰密钥的命令，再扫三根与全部输出。"""
    code_root, data_root, env = installed
    settings = data_root / "settings.json"
    s = json.loads(settings.read_text())
    secrets = [s["backend.api_key"], s["api.token"]]
    needles = secrets + [v[:8] for v in secrets]

    outputs = []
    for argv in (("bootstrap", "keys"), ("config", "ls"), ("config", "get", "api.token"),
                 ("config", "diff"), ("up", "qwen", "--dry-run"), ("up", "api", "--dry-run"),
                 ("model", "repair"), ("model", "info", "qwen"), ("status",), ("status", "--json")):
        r = _mlx(code_root, env, *argv)
        if argv == ("up", "api", "--dry-run"):
            # 此前这一步的 rc 没人断言——密钥、settings 都齐全时它必须 rc 0；
            # 不查 rc 就发现不了这一步本身悄悄坏掉（controller ruling，Task 11）。
            assert r.returncode == 0, f"`up api --dry-run` rc={r.returncode}：{r.stdout + r.stderr}"
        if argv == ("config", "get", "api.token"):
            # 此前这里跑的是 `config get --key=api.token`——缺位置参数，rc 2，输出只有用法，
            # 「没泄漏」是因为命令根本没执行到读密钥那一步（docs-refresh X1）
            assert r.returncode == 0, f"`config get api.token` rc={r.returncode}：{r.stdout + r.stderr}"
            assert "****" in r.stdout, r.stdout
        outputs.append((" ".join(argv), r.stdout + r.stderr))
    leaked_out = [cmd for cmd, text in outputs if any(n in text for n in needles)]
    Cardinality.check("输出里出现密钥的命令", leaked_out, rel="empty")

    scanned, leaked = [], []
    root = code_root.parent
    for base in (code_root, data_root, root / "weights"):
        for p in base.rglob("*"):
            if p.is_symlink() or not p.is_file() or p == settings or ".venv" in p.parts:
                continue
            if p.stat().st_size > 50 * 1024 * 1024:
                continue
            scanned.append(p)
            data = p.read_bytes()
            if any(n.encode() in data for n in needles):
                leaked.append(str(p))
    Cardinality.require_nonempty("被扫描的文件", scanned, "一个文件都没扫到 ⇒ 空真")
    Cardinality.check("含密钥的文件", leaked, rel="empty")


def test_l3_exposure_hint_only_when_bind_not_explicit(tmp_path, repo):
    """spec §4.1「升级提示」：沿用默认（未显式设 api.bind）⇒ 醒目提示新暴露面；显式设过 ⇒ 不打扰。"""
    def install(sub, settings=None):
        roots = {k: tmp_path / sub / v for k, v in
                 (("MLX_ROOT_CODE", "code"), ("MLX_ROOT_DATA", "data"),
                  ("MLX_ROOT_WEIGHTS", "weights"))}
        if settings is not None:
            roots["MLX_ROOT_DATA"].mkdir(parents=True)
            (roots["MLX_ROOT_DATA"] / "settings.json").write_text(json.dumps(settings))
        env = dict(os.environ, PATH="/usr/bin:/bin", **{k: str(v) for k, v in roots.items()})
        r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                           capture_output=True, text=True, timeout=60, check=False, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout + r.stderr

    fresh = install("fresh")
    assert "0.0.0.0" in fresh and "mlx config set api.bind --value=127.0.0.1" in fresh
    pinned = install("pinned", {"api.bind": "127.0.0.1"})
    assert "api.bind 已显式设置" in pinned
    assert "mlx config set api.bind --value=127.0.0.1" not in pinned
