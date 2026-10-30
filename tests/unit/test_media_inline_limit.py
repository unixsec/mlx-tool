"""内联媒体（data URI / 裸 base64 / Anthropic base64）也受 `media_max_mb` 的单件上限。

此前只有代取的媒体按 `media_max_mb` 限；内联的一件可以大到 `max_body_mb`（256 MB）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import base64, json, os, tempfile
from gateway import media as M
root = tempfile.mkdtemp()
store = M.Store(os.path.join(root, "media"))
LIMITS = {"media_max_mb": 1, "media_max_parts": 32}
big = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
small = base64.b64encode(b"x" * 1000).decode()
def go(part, family="chat"):
    if family == "messages":
        body = {"model": "m", "messages": [{"role": "user", "content": [part]}]}
    else:
        body = {"model": "m", "messages": [{"role": "user", "content": [part]}]}
    t = M.Ticket(M.Budget(1 << 30), store)
    try:
        M.process(body, family, "LOOPBACK", [], LIMITS, t, store, fetcher=None)
        return "ok"
    except M.MediaError as e:
        return e.reply()[0]
    finally:
        t.close()
cases = {
    "image": {"type": "image_url", "image_url": {"url": "data:image/png;base64," + big}},
    "audio_uri": {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + big, "format": "wav"}},
    "audio_raw": {"type": "input_audio", "input_audio": {"data": big, "format": "wav"}},
    "video": {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + big}},
}
out = {k: go(v) for k, v in cases.items()}
out["anthropic"] = go({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": big}},
                      "messages")
out["small"] = go({"type": "image_url", "image_url": {"url": "data:image/png;base64," + small}})
print(json.dumps(out))
"""


def test_inline_media_parts_are_capped_by_media_max_mb(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60)
    for k in ("image", "audio_uri", "audio_raw", "video", "anthropic"):
        assert got[k] == 413, f"内联 {k} 超过 media_max_mb 应 413：{got}"
    assert got["small"] == "ok", got
