"""L3 · shell rc 的 PATH 写入（R-DEPLOY-24）。

**这些用例先于实现写成，且必须先红。** 理由：本条需求的四条硬约束，每一条
都对应一条会毁掉用户 rc 的真实路径，而它们全都**在干净夹具上不可见**：

  · 夹具若只有 mlx 自己的内容，前缀匹配 vs 全等比较**行为相同**
  · 夹具若不含外来标记块，「删掉 53 行别人的配置」这个后果无处显现
  · 过滤程序中途失败的路径，正常安装永远走不到

所以夹具必须是**真实 rc 的形状**：含一个外来标记块、含外来 PATH 行。
"""
import os
import subprocess

import pytest

pytestmark = pytest.mark.e2e

# 真实 rc 的形状：外来标记块 + 外来 PATH + 用户自己手加的 mlx PATH。
# 本机 ~/.zshrc 就是这个形状（一个 53 行的 `# >>> MBP Full Life Setup >>>` 块）。
REAL_SHAPED_RC = """\
# ~/.zshrc
export EDITOR=vim

# >>> Some Other Tool >>>
# Do not edit between markers
export OTHER_HOME="$HOME/.other"
export PATH="$OTHER_HOME/bin:$PATH"
alias k='kubectl'
# <<< Some Other Tool <<<

export PATH="$HOME/bin:$PATH"

# === mlx tool ===
export PATH="{code_root}/bin:$PATH"

alias ll='ls -la'

# 用户重复加过一次（换机器 / 复制粘贴时很常见）。
# 这一行的存在让「一次最多接管一行」可被求值 —— 只有一行时，
# 限与不限行为相同，变异测不出来。
export PATH="{code_root}/bin:$PATH"
"""


def _install(repo, tmp_path, rc_path, extra_env=None):
    env = dict(os.environ, PATH="/usr/bin:/bin",
               MLX_ROOT_CODE=str(tmp_path / "code"),
               MLX_ROOT_DATA=str(tmp_path / "data"),
               MLX_ROOT_WEIGHTS=str(tmp_path / "weights"),
               MLX_RC_FILES=str(rc_path))
    env.update(extra_env or {})
    return subprocess.run(["bash", str(repo / "v7" / "deployer" / "install.sh")],
                          capture_output=True, text=True, timeout=120,
                          check=False, env=env)


@pytest.fixture()
def rc(tmp_path):
    p = tmp_path / "zshrc"
    p.write_text(REAL_SHAPED_RC.format(code_root=tmp_path / "code"))
    return p


def test_l3_rc_foreign_block_survives_verbatim(repo, tmp_path, rc):
    """**外来标记块逐字节不变。** 前缀匹配块边界的实现在这里必红。

    坏样本：用 `/^# >>> /` 识别边界 ⇒ skip 开在外来块的 begin、
    关不上（它的 end 是 `# <<< Some Other Tool <<<`，与 mlx 的 end 不等）
    ⇒ 从外来块开始到 mlx 块结束之间**全部内容被删**，而 rc == 0。
    """
    before = rc.read_text()
    foreign = before[before.index("# >>> Some Other Tool >>>"):
                     before.index("# <<< Some Other Tool <<<") + len("# <<< Some Other Tool <<<")]
    r = _install(repo, tmp_path, rc)
    assert r.returncode == 0, r.stderr
    after = rc.read_text()
    assert foreign in after, (
        "外来标记块被破坏 —— 这是数据丢失，不是格式问题。\n"
        f"改动后的文件：\n{after}")
    for keep in ("export EDITOR=vim", "alias k='kubectl'", "alias ll='ls -la'",
                 'export PATH="$HOME/bin:$PATH"'):
        assert keep in after, f"块外内容丢失：{keep!r}"


def test_l3_rc_takeover_only_touches_whitelisted_lines(repo, tmp_path, rc):
    """**接管面 == 整行全等于白名单的行**，一行都不能多。

    这是爆炸半径的判据。它限制的是**内容**不是**数量**：
    计数限制（「最多接管一行」）拦不住「一行删错」，却会在用户 rc 里有
    重复 PATH 行时制造不幂等 —— 见 R-DEPLOY-24 的实现期订正。
    """
    before = rc.read_text().splitlines()
    r = _install(repo, tmp_path, rc)
    assert r.returncode == 0, r.stderr
    after = rc.read_text().splitlines()

    # 夹具里有**两行**相同的 mlx PATH（用户重复加过）。两行都该被吸收进标记块。
    path_line = f'export PATH="{tmp_path / "code"}/bin:$PATH"'
    assert before.count(path_line) == 2, "夹具应含两行相同的 mlx PATH"

    removed = [ln for ln in before if ln not in after]
    # **判据是内容**：删掉的每一行都必须与白名单全等
    whitelist = {path_line, "# === mlx tool ==="}
    illegal = [ln for ln in removed if ln not in whitelist]
    assert not illegal, f"删掉了白名单之外的行（爆炸半径失控）：{illegal}"
    assert all("mlx" in ln.lower() for ln in removed), (
        f"删掉了与 mlx 无关的行：{[ln for ln in removed if 'mlx' not in ln.lower()]}")


def test_l3_rc_idempotent_across_two_installs(repo, tmp_path, rc):
    """连装两次 → 标记块恰好一份。"""
    assert _install(repo, tmp_path, rc).returncode == 0
    once = rc.read_text()
    assert _install(repo, tmp_path, rc).returncode == 0
    twice = rc.read_text()

    begins = twice.count("# >>> mlx")
    assert begins == 1, f"标记块出现 {begins} 次，应恰好 1 次\n{twice}"
    assert once == twice, "第二次安装改变了 rc —— 不幂等"


def test_l3_rc_backup_exists_and_matches_original(repo, tmp_path, rc):
    """**改动前必须备份**，且备份与安装前逐字节相同。

    备份落数据根（卸载时保留 ⇒ 天然可回滚），不得写 $HOME 根。
    """
    original = rc.read_text()
    r = _install(repo, tmp_path, rc)
    assert r.returncode == 0, r.stderr

    backups = sorted((tmp_path / "data" / "backups").glob("*")) \
        if (tmp_path / "data" / "backups").is_dir() else []
    assert backups, "没有备份 —— R-DEPLOY-24 要求改动任何 rc 之前先备份"
    assert any(b.read_text() == original for b in backups), (
        f"备份内容与安装前不同：{[b.name for b in backups]}")


def test_l3_rc_no_marker_literal_in_scripts(repo):
    """**脚本里不得出现标记串字面量**（G-RES-15 / T5 第 12 行）。

    标记串必须取自 `constants.env` 的完整串。写死字面量会废掉这条防护：
    v6 与 v7 共存时，前缀匹配会让它们互相误删 —— T5 原话「这是数据丢失路径，
    不是洁癖」。
    """
    counts = {}
    for name in ("install.sh", "uninstall.sh"):
        src = (repo / "v7" / "deployer" / name).read_text()
        counts[name] = sum(1 for line in src.splitlines()
                           if not line.strip().startswith("#") and ">>> " in line)

    # `install.sh` 是**唯一构造点**：它产生 constants.env，必须在那里构造一次。
    assert counts["install.sh"] <= 2, (
        f"install.sh 里标记串出现 {counts['install.sh']} 次；"
        f"构造点应只有一处（begin/end 各一行）")
    # `uninstall.sh` 是**读者**，一次都不该有 —— 它必须从 constants.env 读完整串。
    # 写死字面量会让 v6/v7 共存时互相误删（T5 第 12 行：这是数据丢失路径）。
    assert counts["uninstall.sh"] == 0, (
        "uninstall.sh 里写死了标记串字面量；它是读者，必须从 constants.env 读")


def test_l3_rc_not_truncated_when_filter_dies(repo, tmp_path, rc):
    """**过滤程序中途失败时，rc 文件不得变短。**

    坏样本：`awk ... > tmp` 之后无条件 `mv tmp rc`。awk 被信号杀死时输出
    全缓冲、一字节未 flush ⇒ tmp 为空 ⇒ 追加三行标记块 ⇒ mv ⇒
    **86 行的 .zshrc 变成 3 行**，而函数返回 0。
    这里用一个必然失败的假 awk 来触发那条路径。
    """
    original = rc.read_text()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "awk").write_text("#!/bin/bash\nexit 1\n")
    (fake_bin / "awk").chmod(0o755)

    _install(repo, tmp_path, rc, extra_env={"PATH": f"{fake_bin}:/usr/bin:/bin"})
    after = rc.read_text()

    assert len(after.splitlines()) >= len(original.splitlines()) - 3, (
        f"rc 文件从 {len(original.splitlines())} 行变成 {len(after.splitlines())} 行 —— "
        f"过滤失败时用截断内容覆盖了用户的文件\n{after}")
    assert "alias ll='ls -la'" in after, "文件尾部内容丢失（典型的截断覆盖）"


def test_l3_rc_block_removed_on_uninstall(repo, tmp_path, rc):
    """卸载后标记块消失，**块外内容逐字节不变**。

    「接管即吸收、卸载不还原」是明写的取舍（R-DEPLOY-24）：用户手工加的那行
    被吸收进标记块，卸载时随块一起消失，不会回来 —— 但安装期已把原文存进备份。
    """
    assert _install(repo, tmp_path, rc).returncode == 0
    after_install = rc.read_text()
    assert "# >>> mlx_server:" in after_install

    r = subprocess.run(["bash", str(repo / "v7" / "deployer" / "uninstall.sh")],
                       capture_output=True, text=True, timeout=60, check=False,
                       env=dict(os.environ, PATH="/usr/bin:/bin", MLX_ASSUME_YES="1",
                                MLX_ROOT_CODE=str(tmp_path / "code"),
                                MLX_ROOT_DATA=str(tmp_path / "data")))
    assert r.returncode == 0, r.stderr
    after = rc.read_text()

    assert "# >>> mlx_server:" not in after, f"标记块没清掉：\n{after}"
    assert "mlx_server/bin" not in after, "PATH 行残留 —— 指向已删除的目录"
    for keep in ("export EDITOR=vim", "alias k='kubectl'", "alias ll='ls -la'",
                 "# >>> Some Other Tool >>>", 'export PATH="$HOME/bin:$PATH"'):
        assert keep in after, f"卸载破坏了块外内容：{keep!r}\n{after}"


def _install_with_fake_awk(repo, tmp_path, rc, script_body):
    """用一个假 awk 触达「退出码为 0 但输出坏了」的路径。

    真实成因：awk 输出是全缓冲的，被信号杀死时可能一字节未 flush；
    磁盘满、EPERM 也会产生「部分输出 + 非零或零退出码」的组合。
    第一道防线（检查退出码）拦不住 rc == 0 的那些。
    """
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "awk").write_text(script_body)
    (fake / "awk").chmod(0o755)
    return _install(repo, tmp_path, rc, extra_env={"PATH": f"{fake}:/usr/bin:/bin"})


def test_l3_rc_rejects_empty_output_with_zero_exit(repo, tmp_path, rc):
    """过滤程序**成功退出但输出为空** → 必须拒绝替换。

    这是「86 行的 .zshrc 变成 3 行」那条路径的精确形态：
    tmp 为空 → 追加三行标记块 → mv → 用户的 rc 只剩标记块。
    退出码那道防线在这里是瞎的（rc == 0）。
    """
    original = rc.read_text()
    _install_with_fake_awk(repo, tmp_path, rc, "#!/bin/bash\nexit 0\n")
    assert rc.read_text() == original, (
        "过滤产出为空时替换了用户的 rc —— 这正是「文件被截断成三行」的路径")


def test_l3_rc_rejects_partial_output_with_zero_exit(repo, tmp_path, rc):
    """过滤程序**成功退出但只输出了一部分** → 必须拒绝替换。

    比上一条更隐蔽：产出非空，退出码为 0，只是行数不对。
    只有「删除行数恰好等于预期」这条等式能拦住它 ——
    用「行数下界」拦不住（少输出的行数可能仍在下界之上）。
    """
    original = rc.read_text()
    _install_with_fake_awk(repo, tmp_path, rc,
                           "#!/bin/bash\nprintf 'only one line\\n'\nexit 0\n")
    assert rc.read_text() == original, (
        "过滤产出行数不对时替换了用户的 rc")


def test_l3_rc_uninstall_when_file_contains_only_our_block(repo, tmp_path):
    """rc 文件**只含本工具标记块**时，卸载必须清得掉。

    坏样本（本轮真实发生）：卸载侧用 `[ -s "$tmp" ]` 判过滤结果 ——
    全删后为空是**正确结果**，却被判为「过滤失败」⇒ 标记块永远留着，
    指向一个已被删除的目录。安装侧修过同一个坑，卸载侧漏了：
    **同一条判据写在两处，只改一处。**
    """
    import subprocess
    code, data = tmp_path / "code", tmp_path / "data"
    rc_path = tmp_path / "zshrc"
    rc_path.write_text("")

    assert _install(repo, tmp_path, rc_path).returncode == 0
    assert "# >>> mlx_server:" in rc_path.read_text()

    r = subprocess.run(["bash", str(repo / "v7" / "deployer" / "uninstall.sh")],
                       capture_output=True, text=True, timeout=60, check=False,
                       env=dict(os.environ, PATH="/usr/bin:/bin", MLX_ASSUME_YES="1",
                                MLX_ROOT_CODE=str(code), MLX_ROOT_DATA=str(data)))
    assert r.returncode == 0, r.stderr
    assert "过滤失败" not in r.stderr, f"正常场景被判为失败：{r.stderr}"
    assert rc_path.read_text().strip() == "", (
        f"标记块没清掉，留下的指向已删除的目录：\n{rc_path.read_text()}")


def test_l3_rc_when_file_contains_only_our_lines(repo, tmp_path):
    """rc 文件**只含本工具的内容**时，安装仍须成功。

    坏样本（本轮真实发生）：「产出非空」检查写作 `[ ! -s "$tmp" ] && [ "$nold" -gt 0 ]`
    —— 全删之后 tmp 为空是**正确结果**，而 nold>0 恒成立 ⇒ 正常场景被误拒，
    用户看到「rc 过滤产出为空，未改动」而 PATH 始终没进去。
    判据必须是 `want`（预期剩余行数），不是「原文件非空」。
    """
    code = tmp_path / "code"
    rc_path = tmp_path / "zshrc"
    rc_path.write_text(f'# === mlx tool ===\nexport PATH="{code}/bin:$PATH"\n')

    r = _install(repo, tmp_path, rc_path)
    assert r.returncode == 0, r.stderr
    after = rc_path.read_text()
    assert "# >>> mlx_server:" in after, (
        f"PATH 没写进去 —— 全删场景被误拒：\n{r.stderr}\n{after}")
    assert after.count(f'{code}/bin') == 1, "PATH 行应恰好一份"
