"""C6 · `drafters.tsv` 损坏/不可读时 `model ls` 仍要能用（降级，不整体拒绝）。

坏样本：`model_query._attachment_types` 直接调 `open_tables(ctx).drafters()`，
表坏了就让这条只读列表命令整体 rc 3——用户想看看已登记了哪些模型，却因为
一张跟大多数模型无关的表坏了而看不到任何东西。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"

PROG = WORLD + """
from mlx_server import ui
from mlx_server.cmd import model_query
from mlx_server.cmd import render_model  # noqa: F401  登记渲染器
from mlx_server.cmd.svc import Invocation
seed("txt", "org/txt", 8101, config={})
seed("vis", "org/vis", 8102, config=fixture("qwen38/config.json"))
# 损坏 drafters.tsv：列数与表头（1 列）不符，_read_tsv 会抛 TableError
with open(os.path.join(SHARE, "drafters.tsv"), "a") as fh:
    fh.write("bad\\ttoo\\tmany\\tcolumns\\n")
out = model_query.run(ctx, None, Invocation("ls"), {})
payload = out.payload
print(json.dumps({"rc": out.rc, "modality": {m["id"]: m["modality_display"]
                                              for m in payload["models"]},
                  "drafters_unreadable": payload.get("drafters_unreadable"),
                  "text": ui.human(payload)}))
"""


def test_model_ls_degrades_when_drafters_tsv_unreadable(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=30, FIXTURES=FIX)
    assert got["rc"] == 0, f"drafters.tsv 坏了，model ls 应仍 rc 0：{got}"
    assert got["modality"] == {"txt": "?", "vis": "?"}, (
        f"drafters.tsv 不可读时模态列应全部占位为 ?：{got['modality']}"
    )
    assert got["drafters_unreadable"] is True
    assert "?" in got["text"] and "drafters.tsv" in got["text"], (
        f"人类可读输出里应有一行说明：{got['text']}"
    )
