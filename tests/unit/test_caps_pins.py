"""D3：附属 drafter 的 model_type 集合（drafters.tsv）与 effort 取值顺序钉自 mlx-vlm 0.6.17。
升级上游后这两份副本可能静默过时；`caps show` / `caps refresh` 要指出版本不一致与该复核什么。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json
from mlx_server import caps
same = {"fingerprint": json.dumps({"mlx": "0.32.2", "mlx_lm": "0.31.3", "mlx_vlm": caps.PINNED["mlx_vlm"]})}
newer = {"fingerprint": json.dumps({"mlx": "0.32.2", "mlx_lm": "0.31.3", "mlx_vlm": "0.7.0"})}
print(json.dumps({"same": caps.pin_notes(same), "newer": caps.pin_notes(newer), "none": caps.pin_notes({})}))
"""


def test_pinned_upstream_mismatch_is_reported(payload, sys_py):
    got = run_floor(payload, sys_py, PROG)
    assert got["same"] == [] and got["none"] == [], got
    assert len(got["newer"]) == 1 and "0.7.0" in got["newer"][0] and "drafters.tsv" in got["newer"][0], got
