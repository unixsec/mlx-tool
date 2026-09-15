"""C1 · R2（挂了独立完整 drafter 的 mlx_lm）逐请求**串行**处理（spec §2.4 R2 行）。

`mlx up --dry-run` 的人读判定行此前显示「并发上限 N」，与 R3/R4 一样 ——
夸大了真实的并发能力。改后 R2 显示「串行」，其余规则不变。

真实 `bin/mlx` 子进程 + 真实的 `tests/fixtures/models/qwen38` 作主模型
（`modality=text` 强制走 mlx_lm，即 R2/R4 的分岔点）、一个自造的独立完整
drafter（`architectures` 非空、非 `_mtp`/`speculators_model_type`、不在
`drafters.tsv` 里 ⇒ `standalone=True`、`attachment=False`）。造法参照
`tests/integration/test_l2_backend_route.py` 的 R2 用例与
`tests/e2e/test_dry_run_e2e.py` 的 `installed` 夹具。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"


@pytest.fixture()
def installed_with_drafter(tmp_path: Path, payload: Path):
    import shutil
    share = tmp_path / "share"
    share.mkdir()
    for tsv in sorted((payload / "share").glob("*.tsv")):
        shutil.copy2(tsv, share / tsv.name)
    for d in ("code", "data", "weights"):
        (tmp_path / d).mkdir()
    (share / "constants.env").write_text(
        f"MLX_ROOT_CODE={tmp_path}/code\n"
        f"MLX_ROOT_DATA={tmp_path}/data\n"
        f"MLX_ROOT_WEIGHTS={tmp_path}/weights\n"
        "MLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n"
    )

    data_dir = tmp_path / "data"
    weights_dir = tmp_path / "weights"

    def _seed(mid, repo, port, user_extra, config_text):
        rec = {"id": mid, "user": dict({"repo": repo}, **(user_extra or {})),
               "machine": {"port": port, "revision": "main"}}
        with (data_dir / "models.jsonl").open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        snap = (weights_dir / "hub" / ("models--" + repo.replace("/", "--"))
                / "snapshots" / "main")
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "config.json").write_text(config_text)

    qwen38_cfg = (FIX / "qwen38" / "config.json").read_text()
    # 独立完整 drafter：非空 architectures、普通 model_type ⇒
    # weights.inspect_config 判 standalone=True、attachment=False。
    drafter_cfg = json.dumps({"architectures": ["LlamaForCausalLM"], "model_type": "llama"})

    # "t"：主模型（qwen38 真实夹具，modality=text 强制走 mlx_lm），配了 draft_model ⇒ R2
    _seed("t", "org/t", 8101, {"modality": "text", "draft_model": "d"}, qwen38_cfg)
    # "d"：独立完整 drafter
    _seed("d", "org/d", 8102, {}, drafter_cfg)
    # "t4"：R4 对照（同样 modality=text，但没有 draft_model）
    _seed("t4", "org/t4", 8103, {"modality": "text"}, qwen38_cfg)

    return tmp_path


def _mlx(payload: Path, installed: Path, *argv):
    env = dict(os.environ)
    env.update({"PATH": "/usr/bin:/bin", "MLX_CODE_ROOT": str(payload),
                "MLX_SHARE_ROOT": str(installed / "share")})
    return subprocess.run(
        [str(payload / "bin" / "mlx"), *argv],
        capture_output=True, text=True, timeout=20, check=False, env=env,
    )


def test_r2_dry_run_stderr_says_serial_not_concurrency(payload: Path,
                                                        installed_with_drafter: Path):
    r = _mlx(payload, installed_with_drafter, "up", "t", "--dry-run")
    assert r.returncode == 0, f"rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    assert "R2" in r.stderr, f"没走到 R2：{r.stderr}"
    assert "串行" in r.stderr, f"R2（挂 drafter 的 mlx_lm）判定行应显示「串行」：{r.stderr}"


def test_r4_dry_run_stderr_does_not_say_serial(payload: Path, installed_with_drafter: Path):
    r = _mlx(payload, installed_with_drafter, "up", "t4", "--dry-run")
    assert r.returncode == 0, f"rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    assert "R4" in r.stderr, f"没走到 R4：{r.stderr}"
    assert "串行" not in r.stderr, f"R4（无 drafter）不该显示「串行」：{r.stderr}"
