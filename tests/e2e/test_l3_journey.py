"""L3 · 完整用户旅程跑在**真实安装产物**上。

一条链路串起所有命令：采集能力 → 登记模型 → 列表 → 健康检查 → 启动 → 删除。
每一步都断言可观察行为，不是「没崩就算过」。
"""
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture()
def cli(tmp_path, repo):
    """真实安装，返回一个能跑命令的闭包。"""
    roots = {"MLX_ROOT_CODE": tmp_path / "code",
             "MLX_ROOT_DATA": tmp_path / "data",
             "MLX_ROOT_WEIGHTS": tmp_path / "weights"}
    env = dict(os.environ, PATH="/usr/bin:/bin", MLX_GATEWAY_PORT="8099",
               **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, r.stderr
    code = roots["MLX_ROOT_CODE"]

    # **命令跑在真实用户的环境里：只有 PATH 和 HOME。**
    # 装完的用户只往 rc 文件加一行 PATH —— 既不设 MLX_SHARE_ROOT，
    # 也不设三根（安装器已把它们写进 constants.env，产品该自己去读）。
    # 这里曾经有一行 `env["MLX_SHARE_ROOT"] = ...`，它让每个 L3 测试都替产品
    # 把变量补上，于是「产品找不到自己的 constants.env」这个 P0 全程不可见，
    # 而真实用户 100% 命中它。安装环境与使用环境必须分开，否则测的是前者。
    user_env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}

    def run(*argv, human=False):
        """**默认加 `--json`** —— 测试是机读消费者。

        产品的默认输出是给人看的（附录 A 的「机读」列写的是 `--json`），
        测试要断言结构就必须显式声明自己是机器。`human=True` 时不加，
        用于断言人类可读输出本身。
        """
        argv = list(argv)
        if not human and "--json" not in argv and "--dry-run" not in argv:
            argv.append("--json")
        p = subprocess.run([str(code / "bin" / "mlx"), *argv], capture_output=True,
                           text=True, timeout=30, check=False, env=user_env)
        out = None
        if p.stdout.strip():
            try:
                out = json.loads(p.stdout.strip().splitlines()[-1])
            except ValueError:
                out = p.stdout.strip()
        return p.returncode, out, p.stderr.strip()
    run.code_root = code
    run.data_root = roots["MLX_ROOT_DATA"]
    yield run
    # **无论测试成败都收尾**。此前是 `return run`，测试中途断言失败时
    # 起过的服务留在那里 —— 刚刚真实发生过：一个失败测试留下的网关占着
    # 8099，后面两个测试全部因「Address already in use」失败，
    # 而它们本身没有任何问题。清理失败不该淹没原始失败，所以吞掉异常。
    with contextlib.suppress(Exception):
        run("down", "--all")


def test_l3_journey(cli):
    """一条链路走完，每步断言可观察行为。"""
    steps = []

    # 1 · 采集能力 —— 没有 venv 也要能采集到「没有 venv」这个事实
    rc, out, err = cli("caps", "refresh")
    steps.append(("caps refresh", rc))
    assert rc == 0, f"采集失败：{err}"
    # `refresh` 的主产物按附录 A 2831 是**采集结果差异**，不是全量视图；
    # 基线本身要用查询命令看（`caps show` 返回全量）。
    assert "changes" in out, f"refresh 应返回采集结果差异，实得 {out}"
    rc, shown, err = cli("caps", "show")
    assert rc == 0, err
    assert shown.get("note") == "venv_absent", (
        "没有 venv 时应记下 venv_absent 基线 —— 否则 drift 永远判不出，"
        f"模型服务永远起不来。实得 {shown}")

    # 2 · 登记模型 —— **不要求权重已下载**（register 的用途就是先登记后下载）
    rc, out, err = cli("model", "register", "qwen", "--repo=mlx-community/Q", "--type=text",
                       "--port=8100")
    steps.append(("register", rc))
    assert rc == 0, f"登记失败：{err}"
    assert out["registered"] == "qwen"
    assert out["modality"] == "text", "显式 --type 应写进 modality"

    # 3 · 列表 —— 登记的东西要看得见，且带来源证据
    rc, out, _ = cli("model", "ls")
    steps.append(("ls", rc))
    assert rc == 0 and [m["id"] for m in out["models"]] == ["qwen"]
    assert out["models"][0]["modality"] == "text"
    assert "type_evidence" not in out["models"][0], "登记期不再产出类型证据"

    # 4 · 健康检查 —— 九项都要真的求值，不能报「未注入」
    rc, out, err = cli("check")
    steps.append(("check", rc))
    rows = dict((r[0], r[1]) for r in (out or {}).get("rows", [])) if out else {}
    if not rows:                                   # check 失败时 payload 走 stderr
        assert "venv" in err, f"check 没给出逐项结果：{err}"
    else:
        Cardinality.check("check·九项", sorted(rows), sorted(
            ["code_root", "data_root", "weights_root", "venv", "tables",
             "models_file", "run_dir", "ports", "caps"]), rel="equal")
        assert "检查项未注入" not in json.dumps(rows, ensure_ascii=False), (
            "有检查项没有生产实现 —— cmd.check 只聚合，那九项要在 checks.py 里")
        assert rows["code_root"] == "ok" and rows["tables"] == "ok"

    # 5 · 唯一性 —— 同端口再登记一个必须被拒且零落地
    rc, _, err = cli("model", "register", "other", "--repo=x/y", "--type=text", "--port=8100")
    steps.append(("uniqueness", rc))
    assert rc == 7, f"端口冲突未被拒：rc={rc} {err}"
    rc, out, _ = cli("model", "ls")
    assert [m["id"] for m in out["models"]] == ["qwen"], "被拒的登记仍然落了盘"

    # 6 · 启动 —— 权重不在盘上（这条链路自始至终没下载过），
    # 校验段第 3 步「权重解析」判定确定失败 ⇒ **Refused(6) 且零落地**。
    # 此前这里断言 rc 1/10（起了进程再死），那是「权重解析」还是空桩时的行为：
    # 工具会拿短名当 HuggingFace 仓库名去网上查，401 之后进程才退出，
    # 沿途还留下 run/<id>.json 要用户收敛。提前拒绝严格更好。
    run_before = sorted(os.listdir(os.path.join(str(cli.data_root), "run"))) \
        if os.path.isdir(os.path.join(str(cli.data_root), "run")) else []
    rc, _, err = cli("up", "qwen")
    steps.append(("up", rc))
    assert rc == 6, f"权重不在盘，预期 rc 6（零落地拒绝），实得 {rc}：{err}"
    assert "已落地" not in err, f"Refused 却报了已落地资源（BCR-R-01）：{err}"
    run_after = sorted(os.listdir(os.path.join(str(cli.data_root), "run"))) \
        if os.path.isdir(os.path.join(str(cli.data_root), "run")) else []
    assert run_after == run_before, f"被拒的 up 仍落了盘：{run_before} → {run_after}"

    # 7 · 删除 —— **确认强度按可恢复性分档**：
    #   不带 `--weights` 只删登记（register 一条命令就回来）⇒ 不问，无 tty 也能删；
    #   带 `--weights` 要动权重（虽可从回收站恢复）⇒ 问，无 tty 且无 `--yes` ⇒ rc 5 零删除。
    # 此前两者受同样的约束，于是 CI 里连「删一行登记」都做不到。
    rc, _, err = cli("model", "rm", "qwen", "--weights")
    steps.append(("rm_weights_no_tty", rc))
    assert rc == 5, f"带 --weights 且无 tty 应拒绝：rc={rc} {err}"
    rc, out, _ = cli("model", "ls")
    assert [m["id"] for m in out["models"]] == ["qwen"], "确认被拒仍删了东西"

    rc, _, err = cli("model", "rm", "qwen")
    steps.append(("rm", rc))
    assert rc == 0, f"不带 --weights 应无需确认：rc={rc} {err}"
    rc, out, _ = cli("model", "ls")
    assert [m["id"] for m in out["models"]] == [], f"删完还在：{out}"

    Cardinality.records.append(("L3·journey 步数", len(steps), None, "informational"))


def test_l3_dry_run_never_touches_disk(cli, tmp_path):
    """`--dry-run` 在真实产物上也不碰盘（`DE-T-018` 的 L3 版）。"""
    cli("model", "register", "qwen", "--repo=x/y", "--type=text", "--port=8100")
    # **权重要真的在盘上**：校验段第 3 步会解析它，`--dry-run` 在整个校验段
    # 之后才分叉（`BCR-4-08`），所以缺权重时 dry-run 也该被拒 —— 那正是
    # 它该预演的结果。这里给它真实前提，测的才是「dry-run 不碰盘」本身。
    snap = tmp_path / "weights" / "hub" / "models--x--y" / "snapshots" / "main"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "config.json").write_text("{}")
    data = tmp_path / "data"
    before = {str(p.relative_to(data)): p.stat().st_mtime
              for p in data.rglob("*") if p.is_file()}
    rc, out, _ = cli("up", "qwen", "--dry-run")
    after = {str(p.relative_to(data)): p.stat().st_mtime
             for p in data.rglob("*") if p.is_file()}
    assert rc == 0 and isinstance(out, list)
    assert out[0] != "mlx_lm", f"argv[0] 仍是包名，不可执行：{out}"
    assert out[1:3] == ["-m", "mlx_lm.server"], out
    assert before == after, f"dry-run 动了盘：{set(after) ^ set(before)}"


def test_l3_down_all_and_uninstall(cli, tmp_path, repo):
    """`down --all` 是**卸载器依赖的形态** —— 它不работает 卸载就会误判「服务未停」。

    坏样本（本次真实发生）：`--all` 不被 cli 接受 ⇒ `down --all` 返回 rc 2
    ⇒ 卸载器把「参数错」读成「服务未能停止」⇒ 永远拒绝卸载。
    """
    rc, out, err = cli("down", "--all")
    assert rc == 0, f"down --all 不可用：rc={rc} {err}"
    assert "targets" in out, f"没给出目标域：{out}"

    rc, out, _ = cli("status", "--all")
    assert rc == 0 and out["targets"] == [], "无服务时 --all 应返回空目标域而非报错"


def test_l3_uninstall_leaves_no_files(cli, tmp_path, repo):
    """卸载后代码根**零文件残留**，数据根**原样保留**。"""
    import subprocess
    cli("model", "register", "keepme", "--repo=x/y", "--type=text")
    code, data = tmp_path / "code", tmp_path / "data"
    assert (data / "models.jsonl").exists()

    env = dict(os.environ, PATH="/usr/bin:/bin", MLX_ASSUME_YES="1",
               MLX_ROOT_CODE=str(code), MLX_ROOT_DATA=str(data))
    r = subprocess.run(["bash", str(repo / "deployer" / "uninstall.sh")],
                       capture_output=True, text=True, timeout=30, check=False, env=env)
    assert r.returncode == 0, f"卸载失败：{r.stderr}"

    left = [str(p) for p in code.rglob("*") if p.is_file()] if code.exists() else []
    Cardinality.check("卸载后代码根残留文件", left, rel="empty")
    assert (data / "models.jsonl").exists(), "数据根被删了 —— 它应该保留"


def test_l3_no_mlx_env_vars_at_all(cli, tmp_path):
    """产品必须在**零 MLX_* 环境变量**下工作 —— 这是真实用户的唯一形态。

    坏样本（本轮真实发生）：`entry` 用 `environ.get("MLX_SHARE_ROOT", "")`，
    取不到时传空串 ⇒ constants.env 变成相对路径 ⇒ open 失败被当作「文件缺失」
    吞掉 ⇒ 三根全部落空 ⇒ 任何命令都 rc=6。
    错误实现能否通过：不能 —— 空串版本在这里第一条断言就红。
    """
    import subprocess
    code = tmp_path / "code"
    for argv, why in [(["version"], "最简命令"),
                      (["status", "--all"], "要读数据根"),
                      (["check"], "要读全部三根")]:
        p = subprocess.run([str(code / "bin" / "mlx"), *argv], capture_output=True,
                           text=True, timeout=30, check=False,
                           env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")})
        assert "constants.env" not in p.stderr, (
            f"{why}（mlx {' '.join(argv)}）找不到 constants.env：{p.stderr.strip()}")
        assert p.returncode != 6, f"{why} 被 Refused(6)：{p.stderr.strip()}"


def test_l3_uninstall_after_running_service(cli, tmp_path, repo):
    """**起过服务之后**再卸载 —— 与「装完就卸」是两条不同的代码路径。

    坏样本（本轮真实发生）：uninstall.sh 的服务收敛块里
    `left=$(ls "$RUN"/*.json | wc -l)` 在 set -euo pipefail 下，
    glob 无匹配时 ls 返回 2 ⇒ pipefail 判负 ⇒ **脚本静默退出**，
    删除步骤一行不跑，而输出看起来和成功一样。
    只有「起过服务」这条路径会创建 run/ 从而进入那个块 ——
    装完就卸的测试永远照不到它。
    """
    import subprocess
    rc, out, err = cli("up", "api")
    assert rc == 0, f"起服务失败：{err}"
    rc, _, err = cli("down", "--all")
    assert rc == 0, f"停服务失败：{err}"

    code, data = tmp_path / "code", tmp_path / "data"
    assert (data / "run").is_dir(), "run/ 应当已被创建 —— 否则这条路径没被覆盖到"

    r = subprocess.run(["bash", str(repo / "deployer" / "uninstall.sh")],
                       capture_output=True, text=True, timeout=60, check=False,
                       env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", ""),
                            "MLX_ASSUME_YES": "1", "MLX_ROOT_CODE": str(code),
                            "MLX_ROOT_DATA": str(data),
                            # 显式构造 env 时必须自带它 —— 否则部署器回落到
                            # `$HOME/.zshrc`，写用户的真实配置文件
                            "MLX_RC_FILES": str(tmp_path / "sandbox-rc")})
    assert r.returncode == 0, f"卸载失败 rc={r.returncode}：{r.stderr}"
    assert "按分发清单移除" in r.stderr, (
        f"删除步骤没有执行 —— 脚本半途退出了：{r.stderr}")

    left = [str(p) for p in code.rglob("*") if p.is_file()] if code.exists() else []
    Cardinality.check("起过服务后卸载，代码根残留文件", left, rel="empty")


def test_l3_dry_run_never_crashes_for_any_verb(cli, tmp_path):
    """**每个动词的 `--dry-run` 都不得崩。**

    坏样本（本轮真实发生）：`entry` 的 dry-run 分支无条件取 `payload["argv"]`，
    那是启动类动词的形态；`mlx install --dry-run` 于是 KeyError ——
    而 `--dry-run` 正是用户在**不确定会发生什么**时才用的那个开关，
    在那里给他一个 traceback 是最坏的时机。

    这条守卫**遍历动词表**，不是逐个手写用例：新增动词自动被覆盖。
    """
    import csv
    rows = list(csv.DictReader(
        (l for l in (tmp_path / "code" / "share" / "verbs.tsv").read_text().splitlines()
         if l and not l.startswith("#")),
        fieldnames=["command", "subcommand", "impl", "positionals",
                    "visibility", "summary"], delimiter="\t"))
    assert len(rows) >= 10, "动词表没读对"

    crashed = []
    for r in rows:
        if r["visibility"] != "public" or r["impl"] == "cli":
            continue
        argv = [r["command"]]
        if r["subcommand"] != "-":
            argv.append(r["subcommand"])
        if r["positionals"] in ("1", "+"):
            argv.append("nonexistent-target")
        rc, out, err = cli(*argv, "--dry-run")
        if "Traceback" in err:
            crashed.append(f"mlx {' '.join(argv)} --dry-run: {err.splitlines()[-1]}")

    Cardinality.check("--dry-run 崩掉的动词", crashed, rel="empty")


def test_l3_status_dual_semantics(cli, tmp_path):
    """`mlx status` 的两个作用域，各带 `kind` 供机读消费者分派。

    **不带目标**必须给出三根与 PATH 生效性 —— 这是「装完了但 mlx 找不到」
    这个最常见首次使用故障的唯一诊断入口（`verbs.tsv` 第 4 行的判据）。
    **两种形状不同而 `--json` 只有一个**，所以包络必须带判别字段（评审 P1-4）：
    没有它，`mlx status --json | jq '.[0].port'` 在两种形态下一个可用一个报错。
    """
    rc, tool, err = cli("status")
    assert rc == 0, err
    assert tool["kind"] == "tool", f"不带目标应是工具语义：{tool}"
    for key in ("roots", "roots_exist", "path_effective", "bin_dir", "manifest"):
        assert key in tool, f"工具状态缺字段 {key!r}：{sorted(tool)}"
    assert set(tool["roots"]) == {"code", "data", "weights"}, "必须给出三根"
    assert tool["manifest"]["missing"] == [], (
        f"刚装完就有清单缺项：{tool['manifest']['missing']}")

    rc, svc, err = cli("status", "api")
    assert rc == 0, err
    assert svc["kind"] == "service", f"带目标应是服务语义：{svc}"
    assert svc["service"] == "api"

    # 空结果表现：不带目标时**恒非空**（「合法空数组」只对模型行那一段成立）
    assert tool != [] and tool != {}, "不带目标的 status 不得为空 —— 它必须能诊断"


def test_l3_default_output_is_human_readable(cli):
    """**默认输出给人看，`--json` 才给机器看。**

    附录 A 的「机读」列写的是 `--json` —— 意思就是默认**不是** JSON。
    坏样本（本轮真实发生）：实现反了，`mlx status` 默认吐一行压缩 JSON，
    用户第一次跑就说「一点也不直观」。

    错误实现能否通过：默认吐 JSON ⇒ 本用例第一条断言红。
    """
    rc, out, err = cli("status", human=True)
    assert rc == 0, err
    assert not out.lstrip().startswith("{"), (
        f"默认输出是 JSON —— 应该是人类可读的：\n{out[:200]}")
    # 人类可读的判据不是「不是 JSON」，而是**这几件事一眼能看到**
    for must in ("代码根", "数据根", "权重根", "PATH", "分发清单", "模型", "网关"):
        assert must in out, f"人类可读输出里看不到「{must}」：\n{out}"

    rc, js, err = cli("status")        # fixture 默认加 --json
    assert isinstance(js, dict) and js["kind"] == "tool", "--json 必须仍是机读"


def test_l3_check_failure_keeps_the_nine_rows(cli):
    """**`check` 失败时主产物不得丢。**

    坏样本（本轮真实发生）：失败走 `Failed` ⇒ 九行主产物被丢掉，
    用户只看到一行 Python repr（`check.failed（failed=[...]；rows=[(...)]）`），
    看不出是哪几项挂了、更不知道该做什么。
    `Aggregate` 携带 payload 就是为了这个。
    """
    rc, out, err = cli("check", human=True)
    assert rc in (0, 64, 65), f"check 的 rc 应 ∈ {{0,64,65}}，实得 {rc}"
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) >= 9, f"九项主产物丢了，只剩 {len(lines)} 行：\n{out}"
    assert "rows=[" not in out and "failed=[" not in out, (
        f"主产物里出现了 Python repr：\n{out}")
    if rc != 0:
        # 失败项必须给出**可操作的下一步**，不是一个裸 False
        bad = [l for l in lines if "✗" in l or "failed" in l]
        assert bad, "rc 非零却没有任何失败行"
        for l in bad:
            assert "False" not in l, f"detail 是裸 False，没说该做什么：{l}"


def test_l3_no_bare_message_keys_in_human_output(cli, tmp_path):
    """**人类可读输出里不得出现裸文案键**（L1·ui-10）。

    坏样本（本轮真实发生）：`mlx api info` 打印 `api.honest_no_boundary` ——
    那是 messages.tsv 的键，表里明明有文案，只是没人去查。
    成因是 `ui._text` 依赖 `ui.bind()` 的装配，而 `entry` 从未调用它 ⇒
    查表恒失败、恒回落到 key 本身。渲染是纯函数，不该因装配顺序时灵时不灵。

    本用例**遍历动词表**跑每个 public 动词，扫输出里的裸键。
    """
    import csv
    import re
    rows = list(csv.DictReader(
        (l for l in (tmp_path / "code" / "share" / "verbs.tsv").read_text().splitlines()
         if l and not l.startswith("#")),
        fieldnames=["command", "subcommand", "impl", "positionals",
                    "visibility", "summary"], delimiter="\t"))
    keys = {r.split("\t")[0] for r in
            (tmp_path / "code" / "share" / "messages.tsv").read_text().splitlines()
            if r.strip() and not r.startswith("#")}
    assert keys, "messages.tsv 没读到键 ⇒ 本用例空真"

    cli("up", "api")
    leaked = []
    for r in rows:
        if r["visibility"] != "public" or r["impl"] == "cli":
            continue
        argv = [r["command"]] + ([r["subcommand"]] if r["subcommand"] != "-" else [])
        if r["positionals"] in ("1", "+"):
            argv.append("api" if r["command"] in ("up", "down", "restart") else "nope")
        if argv[0] in ("uninstall", "install"):
            continue                    # 会动盘，另有用例
        rc, out, err = cli(*argv, human=True)
        for stream, name in ((out, "stdout"), (err, "stderr")):
            if not isinstance(stream, str):
                continue
            for m in re.finditer(r"\b([a-z][a-z_]*\.[a-z][a-z_.]+)\b", stream):
                if m.group(1) in keys:
                    leaked.append(f"mlx {' '.join(argv)} 的 {name}: {m.group(1)}")
    cli("down", "--all")
    Cardinality.check("人读输出里的裸文案键", sorted(set(leaked)), rel="empty")


def test_l3_no_verb_crashes_on_real_invocation(cli, tmp_path):
    """**每个只读动词真跑一次都不得 Traceback**（不是 dry-run，是真调用）。

    坏样本（本轮真实发生，两个）：
      · `mlx bench <id>` 把模型 id 当端口传给 `send_chat(port, messages)` ⇒
        URL 变成 `http://127.0.0.1:demo1/…` ⇒ ValueError 崩到用户脸上；
      · `mlx bootstrap verify` 要的 `tables` 从没被 `build_deps` 注入 ⇒
        恒报「读不到 share/ 下的表」，而表明明在。

    **两个都是签名/装配对不上，却活到了现在** —— 因为既有测试只覆盖
    dry-run 和主线旅程，这些命令从未被真正调用过。
    本用例遍历动词表，新增动词自动纳入。
    """
    import csv
    rows = list(csv.DictReader(
        (l for l in (tmp_path / "code" / "share" / "verbs.tsv").read_text().splitlines()
         if l and not l.startswith("#")),
        fieldnames=["command", "subcommand", "impl", "positionals",
                    "visibility", "summary"], delimiter="\t"))
    assert len(rows) >= 10, "动词表没读对"

    cli("model", "register", "probe1", "--repo=x/y", "--type=text", "--port=8100")
    # 会动盘或需要外部资源的动词单独有用例，这里只跑只读面
    skip = {"install", "uninstall", "up", "down", "restart", "chat"}
    crashed = []
    for r in rows:
        if r["visibility"] != "public" or r["impl"] == "cli" or r["command"] in skip:
            continue
        argv = [r["command"]] + ([r["subcommand"]] if r["subcommand"] != "-" else [])
        if r["subcommand"] in ("install", "register", "rm"):
            continue                      # 有副作用，另有用例
        if r["positionals"] in ("1", "+"):
            argv.append("probe1")
        rc, out, err = cli(*argv, human=True)
        if isinstance(err, str) and "Traceback" in err:
            crashed.append(f"mlx {' '.join(argv)}: {err.strip().splitlines()[-1]}")
    Cardinality.check("真调用会崩的动词", crashed, rel="empty")


def test_l3_up_reports_failure_when_port_is_taken(cli, tmp_path):
    """**端口被别的进程占着时，`up` 必须报失败，不得报 started。**

    坏样本（本轮真实发生）：`spawn` 成功就 `Done` ⇒ rc=0、stdout「已启动」，
    而进程三秒后因 `Address already in use` 退出，下一条 `mlx status` 说「未运行」。
    这是 v6 那 18 条「报成功但没产生效果」的形态在 v7 的复现。

    第一次修还修错了：就绪判据用 `probe.listener_pid`（端口上有没有人），
    而占着端口的正是那个无关进程 ⇒ 照样判为就绪。
    判据必须是 `port_owner`（**监听者是不是我们记录的 pid/pgid**）——
    那正是设计里 port_owner 存在的理由。

    错误实现能否通过：spawn 完就 Done ⇒ rc=0 ⇒ 本用例红。
    """
    import socket

    const = (tmp_path / "code" / "share" / "constants.env").read_text()
    gw_port = int(next(l.split("=", 1)[1] for l in const.splitlines()
                       if l.startswith("MLX_GATEWAY_PORT=")))

    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("0.0.0.0", gw_port))
    holder.listen(1)
    try:
        rc, out, err = cli("up", "api", human=True)
        assert rc != 0, (
            f"端口被占却报成功（rc={rc}）—— 报成功但没产生效果\nstdout={out}")
        assert rc == 10, f"应为 rc 10（起来后立刻退出），实得 {rc}"
        assert "端口" in err, f"失败原因里没提端口：{err}"
        assert "已启动" not in str(out), f"stdout 仍在报已启动：{out}"
    finally:
        holder.close()


def test_l3_model_rm_confirm_works_on_real_tty(cli, tmp_path):
    """**确认通道必须在真 tty 上可用**（附录 A `model rm` 的过程流：
    「逐行列出目标 + 确认串」）。

    坏样本（本轮真实发生）：`open("/dev/tty", "r+")` 打得开，但同一个
    buffered 流上 write 之后 readline 需要 seek，而 tty 不可 seek ⇒
    抛 `io.UnsupportedOperation`；它继承自 `OSError`，被 `except OSError`
    吞掉 ⇒ 恒返回 False ⇒ **`mlx model rm` 完全不可用，提示串一次都没打印过**。

    这条能活到现在，是因为没有任何测试驱动过真 tty ——
    管道/DEVNULL 下 EOF 视为拒绝是**设计要求的正确行为**，所以旧测试全绿。
    本用例用 `pty.fork` 起一个真终端。
    """
    import os
    import pty
    import subprocess
    import time

    cli("model", "register", "rmtest", "--repo=x/y", "--type=text", "--port=8100")
    code = tmp_path / "code"

    # **用 openpty + subprocess，不用 pty.fork** —— 后者会 fork 出一份
    # pytest 自己的副本，exec 失败时那份副本会继续跑测试代码，
    # 且父进程侧的 fd 不关会泄漏，干扰同一会话里后续的用例（实测发生过）。
    primary, secondary = pty.openpty()
    try:
        proc = subprocess.Popen(
            # **带 `--weights`** —— 确认强度按可恢复性分档之后，只有这一档还问。
            # 不带的那档已经不需要 tty（登记可完全恢复，确认在那里是纯摩擦）。
            [str(code / "bin" / "mlx"), "model", "rm", "rmtest", "--weights"],
            stdin=secondary, stdout=secondary, stderr=secondary,
            env={"HOME": os.path.expanduser("~"),
                 "PATH": f"{code / 'bin'}:/usr/bin:/bin"},
            start_new_session=True)
        os.close(secondary)
        time.sleep(1.0)
        os.write(primary, b"rmtest\n")
        out = b""
        try:
            while True:
                chunk = os.read(primary, 4096)
                if not chunk:
                    break
                out += chunk
        except OSError:
            pass
        rc = proc.wait(timeout=30)
    finally:
        os.close(primary)
    text = out.decode("utf-8", "replace")

    assert "确认" in text, f"确认提示一次都没打印：\n{text}"
    assert "model.confirm" not in text, f"把文案键当成了要键入的确认串：\n{text}"
    assert rc == 0, f"键入了正确的确认串却没删成（rc={rc}）：\n{text}"

    rc2, listing, _ = cli("model", "ls")
    ids = [m["id"] for m in (listing or {}).get("models", [])]
    assert "rmtest" not in ids, f"确认通过了但记录还在：{ids}"


def test_l3_status_and_api_info_have_all_contracted_fields(cli):
    """**附录 A 逐字列出的字段，一个都不能少。**

    · `status`（不带目标）：附录 A 2810 / §4.1.2 1071 / 台账 182 三处都写
      「三根 · **版本** · PATH 生效性 · 清单完整性」+ 模型行 + 网关行。
      台账把版本写进了空结果契约的最小字段集（「恒非空：三根 + 版本 +
      PATH 生效性至少四个字段」）—— 它不是装饰，是下限。
    · `api info`：附录 A 2829 要求三项「base url + **可用 id 列表** + 诚实陈述」。

    坏样本（本轮真实发生）：两者各缺一项，而 `mlx help status` 的 summary
    还对用户承诺着「三根·版本·PATH·清单」—— **命令自己的帮助与自己的输出对不上**。
    """
    cli("model", "register", "fieldcheck", "--repo=x/y", "--type=text", "--port=8100")

    rc, tool, err = cli("status")
    assert rc == 0, err
    for key in ("roots", "version", "path_effective", "manifest", "models", "gateway"):
        assert key in tool, f"status 缺附录 A 要求的 {key!r}：{sorted(tool)}"
    assert tool["version"], "version 为空 —— 台账把它写进了最小字段集"

    cli("up", "api")
    rc, api, err = cli("api", "info")
    assert rc == 0, err
    for key in ("base_url", "models"):
        assert key in api, f"api info 缺附录 A 要求的 {key!r}：{sorted(api)}"
    # `models` 现在是**网关真的转发得到的**那些（与 `/v1/models` 同一判据）；
    # 已登记但没起的落在 `registered_only`。分两个列表是因为工具里本来就有
    # 两个集合 —— 合成一个会让用户抄到一个填进客户端却连不上的 id。
    both = list(api.get("models") or []) + list(api.get("registered_only") or [])
    assert "fieldcheck" in both, (
        f"已登记的模型两个列表里都没有：models={api.get('models')} "
        f"registered_only={api.get('registered_only')}")
    assert "fieldcheck" not in (api.get("models") or []), (
        "模型没起，却出现在「网关可用」列表里 —— 用户会抄走一个连不上的 id")
    cli("down", "--all")


def test_l3_nonexistent_id_is_refused_not_silently_ok(cli):
    """**按外部输入索引内部映射之前必须有存在性校验**（R-SAFETY-11 第三层）。

    坏样本（本轮真实发生）：`model gc <不存在的 id>` / `model repair <同>`
    的分支**全程一次都没读过 `inv.target`** —— 不是校验写错，是根本没有；
    对不存在的 id 静默返回 rc 0，用户以为做了什么。
    R-SAFETY-11 的来源栏点名的 v6 症状正是同一形态：「详情命令对未注册短名崩溃」。
    """
    for argv in (["model", "gc", "nosuchmodel"],
                 ["model", "repair", "nosuchmodel"],
                 ["model", "info", "nosuchmodel"],
                 ["model", "rm", "nosuchmodel"]):
        rc, out, err = cli(*argv, human=True)
        assert rc == 6, f"mlx {' '.join(argv)} 应 Refused(6)，实得 rc={rc}\n{err}"
        assert "nosuchmodel" in err, f"错误里没指名那个 id：{err}"


def test_l3_caps_refresh_reports_diff_not_full_view(cli):
    """`caps refresh` 的主产物是**采集结果差异**（附录 A 2831），
    空结果表现是「无变化 → 合法空数组」。

    坏样本（本轮真实发生）：返回全量视图 ⇒ 连跑两次输出逐字节相同，
    用户看不出这次采集**改变了什么**，而这正是 refresh 的全部意义。
    """
    # **安装器现在会采集一次**（边 #3：install.sh 子进程调 `caps refresh`），
    # 所以「首次采集」的前提要自己造 —— 不删的话这里测的是「重复采集」，
    # 与下面那条断言正好相反。
    # 落点在**代码根的 share/**（路径台账），不是数据根
    caps_json = os.path.join(str(cli.code_root), "share", "caps.json")
    if os.path.exists(caps_json):
        os.remove(caps_json)

    rc, first, err = cli("caps", "refresh")
    assert rc == 0, err
    assert "changes" in first, f"没给差异：{first}"
    assert first["changes"], "首次采集应有变化（从无到有）"

    rc, again, err = cli("caps", "refresh")
    assert rc == 0, err
    assert again["changes"] == [], f"无变化时应是合法空数组，实得 {again['changes']}"


def test_l3_config_validate_is_one_row_per_item(cli):
    """`config validate` 的主产物是**每检查项一行**（附录 A 2827），
    退出码 `0,2,6,64,65`（与 check 共用 64/65 那对聚合码）。

    坏样本（本轮真实发生）：只返回一个布尔 `{"valid": true}` ——
    用户不知道校验了哪些项，失败时更不知道是哪一项。
    """
    rc, out, err = cli("config", "validate")
    assert rc in (0, 64, 65), f"rc 应 ∈ {{0,64,65}}，实得 {rc}"
    rows = out.get("rows") or []
    assert len(rows) >= 3, f"逐项一行，实得 {len(rows)} 行：{out}"
    for name, verdict, _detail in rows:
        assert verdict in ("ok", "failed", "undeterminable"), \
            f"{name} 的 verdict 不在判定值域：{verdict}"


def test_l3_multi_target_reports_every_target(cli):
    """**`up`/`down`/`restart` 是多目标动词**，主产物是「每个目标一行结论」。

    依据：附录 A 2807 的动词形态逐字是 `up <service>...`（可变长），
    主产物列逐字是「**每个**目标一行结论（id、port、verdict）」。
    坏样本（本轮真实发生）：实现只取 `positional[0]`，第二个目标被**静默忽略** ——
    用户以为两个都起了。

    另一半同样重要：元数 `1` 的动词必须有**上界**。不补它，多给的 token
    会被静默吞掉；而把 up/down 放宽到 `+` 之后，静默吞掉就变成了
    「把拼错的 flag 当成第二个目标去启动」（`05f-audit-trace.md:32` 逐字推演过
    `mlx up --dry-runn qwen27b` 真的把模型起起来的路径）。
    """
    rc, out, err = cli("up", "api", "nosuchsvc", human=True)
    assert rc != 0, "有目标失败却报成功"
    assert "api" in out and "nosuchsvc" in out, (
        f"逐目标结论不完整 —— 第二个目标被静默忽略了？\n{out}")

    cli("down", "--all")

    # 拼错的 flag 必须被拒，不能变成第二个目标
    rc, out, err = cli("up", "--dry-runn", "api", human=True)
    assert rc == 2, f"拼错的 flag 应 rc 2，实得 {rc}"
    assert "--dry-runn" in err, f"没指名是哪个参数错了：{err}"

    # 元数 1 / ? 的动词有上界
    rc, out, err = cli("status", "api", "extra", human=True)
    assert rc == 2, f"status 最多一个目标，实得 rc={rc}"


def test_l3_commands_survive_real_weights_on_disk(cli, tmp_path):
    """**权重真的在盘上**时，各命令仍须工作。

    坏样本（本轮真实发生）：`weights.resolve` 找到时返回 `Ok(...)`（Probe），
    找不到时返回 `Refused(...)`（Outcome）—— **一个函数返回两个正交类型**，
    而设计第 2 条裁决正是把 `Probe` 与 `Outcome` 拆开的。
    调用方写 `r.rc` 就在找到权重时崩（Probe 没有 `.rc`），
    写 `r.value` 就在找不到时崩。`mlx model register` 与 `mlx model rm --weights`
    两处都中招。

    这个崩溃活到现在，是因为既有的遍历式崩溃守卫跑在**空的权重根**上 ——
    找不到权重那一支恰好是不崩的那支。夹具必须造出真实的权重目录。
    """
    weights_root = tmp_path / "weights"
    snap = weights_root / "hub" / "models--mlx-community--Demo" / "snapshots" / "rev123"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "model.safetensors").write_bytes(b"x" * 1024)

    rc, out, err = cli("model", "register", "wdemo",
                       "--repo=mlx-community/Demo", "--type=text", "--port=8100")
    assert rc == 0, f"权重在盘上时注册失败：{err}"

    rc, info, err = cli("model", "info", "wdemo")
    assert rc == 0, err
    # 台账第 7 行的读路径逐字是 `store.load().record(<id>).machine.revision`
    assert info["machine"].get("revision") == "rev123", (
        f"revision 没落进 machine 段：{info}")

    rc, rep, err = cli("model", "repair")
    assert rc == 0, f"repair 在有真实权重时失败：{err}"
    assert "changes" in rep, f"repair 的主产物应是逐字段差异：{rep}"

    rc, out, err = cli("model", "rm", "wdemo", "--weights", "--yes", human=True)
    assert "Traceback" not in str(err), f"model rm --weights 崩了：{err}"


def test_l3_model_install_does_not_claim_success_without_downloading(cli):
    """**没有下载器时 `model install` 不得报「installed」**。

    坏样本（本轮真实发生）：`weights.download` 的最后一行是
    `return Done(fetch(...))` —— 无条件把 fetch 的返回包成 `Done`，
    而 `fetch` 在 `hf` 不存在时明确返回 `{"ok": False, "reason": "tool_unavailable"}`。
    于是对一个**根本不存在的 repo** 也返回 rc 0 并报「installed」，
    而权重根空空如也 —— 用户以为模型下好了。

    这是「报成功但没产生效果」最直白的一种。附录 A 给 `model install`
    登记的码含 3（外部依赖不可用），正是为这种情形准备的。
    """
    rc, out, err = cli("model", "install", "mlx-community/NoSuchModel-999B",
                       "--type=text", "--port=8299", human=True)
    assert rc != 0, f"没有下载器却报成功（rc={rc}）：{out}"
    assert rc == 3, f"应为 rc 3（外部依赖不可用），实得 {rc}"
    assert "installed" not in str(out), f"仍在报 installed：{out}"
    assert "hf" in err or "下载器" in err, f"没说清为什么下不了：{err}"

    # 记录仍应留下（stage_split 之后失败 ⇒ Failed 带 committed），
    # 且状态必须是「已登记未下载」而不是「就绪」
    rc, listing, _ = cli("model", "ls")
    row = next((m for m in listing["models"]
                if m["id"] == "mlx-community/NoSuchModel-999B"), None)
    assert row is not None, "记录应保留（它是收敛线索）"
    assert row["state"] != "就绪", f"权重没下却标成就绪：{row}"


def test_l3_chat_multi_turn_keeps_stdout_pure(cli):
    """`chat` 的 stdout **逐字节只含回复正文**（台账第 19 行），汇总走 stderr。

    坏样本（本次真实发生的两个）：
    ① 读取循环**从来不存在** —— 不给 `--prompt` 时只开一个空会话就返回
       `turns 0` 且 rc 0，而台账的空结果契约逐字是「空回复 → 零字节，
       且**必须 rc≠0**」，两边正好相反。模块 docstring 却一直写着「REPL」。
    ② 补上循环之后，最后那份汇总（rounds/session/turns）也渲染进了 stdout ——
       `mlx chat x > out.txt` 存下来的文件末尾就多出三行不是回复的东西。

    这里不需要真后端：`chat` 在拿不到端口时就返回 rc 6，
    足以证明「没有输入源时不会假装成功」这一半；有后端的那一半在真机上验过。
    """
    cli("model", "register", "chatty", "--repo=org/chatty", "--type=text", "--port=8161")
    # 没起服务 ⇒ 连不上，但**不能是 rc 0**
    rc, _, err = cli("chat", "chatty", "--prompt=hi", human=True)
    assert rc != 0, f"后端不可达却报成功：rc={rc} {err}"

    # 一轮都没聊成同样不能是 rc 0（台账的空结果契约）
    rc2, _, err2 = cli("chat", "chatty", human=True)
    assert rc2 != 0, f"一轮没聊成却报成功：rc={rc2} {err2}"
