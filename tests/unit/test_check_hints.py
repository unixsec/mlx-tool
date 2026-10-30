"""C15：`mlx check` 的目录项失败时只说「检查权限 / 磁盘挂载」，不是能照做的修法；
判不出的项只给一个 kind（如 PARSE_FAILED）。现在按成因给出带真实路径的命令。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
from mlx_server import checks
from mlx_server.cmd import check
from mlx_server.cmd.svc import Invocation
from mlx_server.ctx import Ctx
from mlx_server.tables import open_tables
missing = os.path.join(_ROOT, "nope-data")
ro = os.path.join(_ROOT, "ro-weights"); os.makedirs(ro); os.chmod(ro, 0o500)
afile = os.path.join(_ROOT, "afile"); open(afile, "w").close()
c = Ctx(_ROOT, missing, ro, VENV, 8340, (8100, 8199), None, share_root=SHARE)
deps = {"tables": open_tables(c), "checks": {"data_root": checks.data_root, "weights_root": checks.weights_root,
        "code_root": lambda x: checks._dir_ok(afile, "代码根")}}
out = check.run(c, None, Invocation("check", None), deps)
rows = {r[0]: [r[1], r[2]] for r in out.payload["rows"]}
os.chmod(ro, 0o700)
print(json.dumps({"rows": rows, "missing": missing, "ro": ro, "afile": afile}, ensure_ascii=False))
"""


def test_directory_check_hints_name_the_path_and_the_command(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60)
    st, d = got["rows"]["data_root"]
    assert st == "failed" and got["missing"] in d and "mkdir -p" in d, got
    st, w = got["rows"]["weights_root"]
    assert st == "failed" and got["ro"] in w and "chmod" in w, got
    st, cd = got["rows"]["code_root"]
    assert st == "failed" and "不是目录" in cd, got
