"""`tv.mutate` —— 每条断言配一条 patch，应用后**必须变红**。

在独立副本里跑，**物理上不可能污染工作区**。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path


class MutationSurvived(AssertionError):
    """变异体没被抓住 ⇒ 那条断言没有区分力。"""


def apply_and_run(repo_root, patch, test_cmd, files):
    """把 patch 应用到**副本**里跑测试。返回 (是否变红, 输出)。

    `files` 是 patch 会碰到的文件列表 —— 只复制它们所在的树，不动原地。
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        shutil.copytree(repo_root, work, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"))
        for rel in files:
            p = work / rel
            p.write_text(patch(p.read_text()))
        r = subprocess.run(test_cmd, cwd=str(work), capture_output=True,
                           text=True, check=False, timeout=180)
        return (r.returncode != 0, r.stdout + r.stderr)


def assert_caught(repo_root, name, patch, test_cmd, files):
    """变异体必须被抓。**没被抓 = 那条断言无区分力**，比测试失败更严重。"""
    caught, out = apply_and_run(repo_root, patch, test_cmd, files)
    if not caught:
        raise MutationSurvived(
            f"变异体 {name!r} 存活 —— 对应断言没有区分力\n{out[-800:]}")
    return True
