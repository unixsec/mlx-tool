"""spec §4.8 / D20：产品模块在 `/usr/bin/python3` 3.9.6 下**真的 import 得起来**。

`check.sh` 的 py_compile 只证明语法；运行期求值的注解（`def f() -> str | None`）、
模块顶层用到 3.10+ 标准库符号，都能通过编译、在 import 时才炸 —— 而网关正跑在这个解释器上。
"""
import json
import subprocess

PROG = r'''
import importlib, json, pkgutil
import gateway, mlx_server
bad, n = [], 0
for pkg in (mlx_server, gateway):
    for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        if m.name == "gateway.__main__":      # 入口模块，import 即启动网关
            continue
        n += 1
        try:
            importlib.import_module(m.name)
        except Exception as exc:              # noqa: BLE001
            bad.append("%s: %r" % (m.name, exc))
print(json.dumps({"n": n, "bad": bad}))
'''


def test_every_product_module_imports_on_floor_interpreter(payload, sys_py):
    r = subprocess.run([sys_py, "-c", PROG], capture_output=True, text=True,
                       timeout=60, check=False,
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{payload}:{payload}/lib"})
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["n"] >= 30, f"只枚举到 {got['n']} 个模块 —— 枚举面失效"
    assert got["bad"] == [], "这些模块在 3.9.6 下 import 失败：\n" + "\n".join(got["bad"])
