"""上游帧夹具是冻结的真实构造结果。测试开头先确认它含聚合器与回程改写要处理的全部形态 ——
夹具缺一种形态，依赖它的测试就会空真通过。"""
import hashlib
import json
from pathlib import Path

FR = Path(__file__).resolve().parents[1] / "fixtures" / "frames"


def test_hash_matches():
    digest, name = (FR / "SHA256SUMS").read_text().split()
    assert name == "frames.json"
    assert hashlib.sha256((FR / name).read_bytes()).hexdigest() == digest, "frames.json 被改过"


def test_fixture_covers_required_shapes():
    f = json.loads((FR / "frames.json").read_text())
    assert f["versions"] == {"mlx_lm": "0.31.3", "mlx_vlm": "0.6.17"}
    lm = f["mlx_lm_chat_stream"]
    assert lm.startswith(": keepalive"), "缺 mlx_lm 的 prefill 保活注释"
    assert '"reasoning"' in lm and "reasoning_content" not in lm, "缺「只有 reasoning」的 mlx_lm 帧"
    assert '"index": 1' in lm and '"logprob": -0.25' in lm and '"choices": [], "usage"' in lm
    assert all("index" not in c for c in f["mlx_lm_chat_nonstream"]["choices"][0]["message"]["tool_calls"])
    assert '"text": " a time"' in f["mlx_lm_completion_stream"]
    vt = f["mlx_vlm_chat_stream_tools"]
    assert '"reasoning_content":"先想"' in vt and '"finish_reason":"tool_calls"' in vt
    assert '"finish_reason":"length"' in f["mlx_vlm_chat_stream_length"]
    err = f["mlx_vlm_chat_stream_error"]
    assert '"error"' in err and "[DONE]" not in err and "/Users/" in err, "缺带路径、无 [DONE] 的中途错误帧"
    rs = f["mlx_vlm_responses_stream"]
    for ev in ("response.created", "response.reasoning_text.delta", "response.output_text.delta",
               "response.output_item.done", "response.completed"):
        assert f"event: {ev}\n" in rs, f"responses 夹具缺 {ev}"
    ms = f["mlx_vlm_messages_stream"]
    for s in ("event: message_start", "thinking_delta", "signature_delta", "text_delta",
              "input_json_delta", '"stop_reason": "tool_use"', "event: message_stop"):
        assert s in ms, f"messages 夹具缺 {s}"
    assert "event: error" in f["mlx_vlm_messages_stream_error"]
    assert [b["type"] for b in f["mlx_vlm_messages_nonstream"]["content"]] == ["thinking", "text", "tool_use"]
    assert f["mlx_vlm_responses_completed"]["output"][0]["type"] == "reasoning"
