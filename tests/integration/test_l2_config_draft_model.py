"""`draft_model` 的值是另一条记录 —— 它能不能用，`config set` / `config validate` 与 `up` 说同一句话。

2026-10-29 真机：`config set qwen38 --key=draft_model --value=qwen38-d`（MTP 附属预测头）rc 0，
`config validate` 也报 ok，到 `up` 才 rc 6 `svc.draft_unsupported`。关系型检查只写在 `up` 的校验段里，
set/validate 只看值域。这里逐条走 `up` 会拒的 drafter 情形，断言 set 当场拒、不落盘，validate 标 failed，
且两边的消息键与 `up` 同一个。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"

PROG = WORLD + r"""
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation
TXT = {"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]}   # 缺 architectures 会被当成附属预测头
seed("txt", "org/txt", 8101, config=TXT)
seed("txt2", "org/txt2", 8102, config=TXT)
seed("vis", "org/vis", 8103, config=fixture("qwen38/config.json"))
seed("mtp", "org/mtp", 8104, config=fixture("qwen38-mtp/config.json"))
seed("nowt", "org/nowt", 8105, config=TXT)
shutil.rmtree(os.path.join(WEIGHTS, "hub", "models--org--nowt"))     # 登记了、权重不在盘
def registry():
    return open(os.path.join(DATA, "models.jsonl"), encoding="utf-8").read()
out = {}
for name, tgt, val in (("attachment", "txt", "mtp"), ("self", "txt", "txt"),
                       ("not_found", "txt", "ghost"), ("weights_missing", "txt", "nowt"),
                       ("vision_conflict", "vis", "txt2"), ("ok", "txt", "txt2")):
    before = registry()
    r = config.run(ctx, None, Invocation("set", tgt, args={"key": "draft_model", "value": val}), {})
    out[name] = {"rc": r.rc, "key": getattr(r, "msg_key", None), "wrote": registry() != before}
# validate：绕过 set 直接写进记录（旧版写进去的、或手改的），validate 要标出来
lines = [json.loads(ln) for ln in registry().splitlines()]
for rec in lines:
    if rec["id"] == "txt2":
        rec["user"]["draft_model"] = "mtp"
open(os.path.join(DATA, "models.jsonl"), "w").write("".join(json.dumps(x) + "\n" for x in lines))
v = config.run(ctx, None, Invocation("validate", "txt2"), {})
out["validate"] = {"rc": v.rc, "row": [list(r) for r in v.payload["rows"] if r[0] == "draft_model"]}
print(json.dumps(out, ensure_ascii=False))
"""

UP_REFUSES = {"attachment": "svc.draft_unsupported", "self": "svc.draft_is_self",
              "not_found": "svc.draft_not_found", "weights_missing": "svc.draft_weights_missing",
              "vision_conflict": "svc.draft_backend_conflict"}


def test_set_and_validate_say_what_up_says_about_the_drafter(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60, FIXTURES=FIX)
    for name, key in UP_REFUSES.items():
        assert got[name] == {"rc": 6, "key": key, "wrote": False}, (name, got[name])
    assert got["ok"]["rc"] == 0 and got["ok"]["wrote"], got["ok"]
    assert got["validate"]["rc"] == 65, got["validate"]
    (row,) = got["validate"]["row"]
    assert row[1] == "failed" and "附属预测头" in row[2], row
