"""spec §4.4 / D8 / §8.2「媒体」：只处理媒体 part，其余逐字节透传；来源分档；临时文件与预算。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
TOOL_TYPES = __TOOL_TYPES__
import base64, copy, json, os, stat, tempfile, time
from gateway import fetch as F
from gateway import media as M

PNG, WAV, MP4 = b"\x89PNG" + b"p" * 100, b"RIFF" + b"w" * 60, b"\x00\x00\x00\x18ftyp" + b"v" * 80
CANNED = {"http://img.test/a.png": ("image/png", PNG), "http://aud.test/a.wav": ("audio/wav", WAV),
          "http://vid.test/../x.sh": ("video/quicktime", MP4), "http://vid.test/odd": ("video/x-foo", MP4),
          "http://vid.test/a.mp4": ("video/mp4", MP4), "http://img.test/b.jpg": ("image/jpeg", PNG),
          "http://10.1.2.3/x.png": ("image/png", PNG)}
CALLS = []
def fake_fetch(url, peer, nets, max_bytes, kinds):
    CALLS.append(url)
    if url.startswith("http://denied.test"):
        raise F.FetchError("address_denied", "10.14.0.7")
    ctype, data = CANNED[url]
    if ctype.split("/")[0] not in kinds:
        raise F.FetchError("bad_content_type", ctype)
    return {"content_type": ctype, "data": data, "host": "x"}
root = tempfile.mkdtemp()
store = M.Store(os.path.join(root, "run", "media"))
LIMITS = {"media_max_mb": 128, "media_max_parts": 32, "inflight_max_mb": 2048}
def go(body, family, peer, limits=LIMITS, budget=None, st=store):
    ticket = M.Ticket(budget or M.Budget(1 << 30), st)
    original = copy.deepcopy(body)
    try:
        out = M.process(body, family, peer, [], limits, ticket, st, fetcher=fake_fetch)
        res = {"ok": out, "dir": ticket.dir, "bytes": ticket.bytes}
    except M.MediaError as e:
        res = {"err": list(e.reply())}
    res["mutated"] = body != original
    res["ticket"] = ticket
    return res
def d64(kind, data): return "data:%s;base64,%s" % (kind, base64.b64encode(data).decode())
TEXT = {"type": "text", "text": "看图 /Users/alex/secret.png"}
def chat(parts): return {"model": "m", "messages": [{"role": "user", "content": [TEXT] + parts}]}
out = {}
for peer in ("LOOPBACK", "REMOTE"):
    b = chat([{"type": "image_url", "image_url": {"url": d64("image/png", PNG)}},
              {"type": "image_url", "image_url": {"url": "http://img.test/a.png", "detail": "high"}},
              {"type": "input_image", "image_url": "http://img.test/b.jpg"},
              {"type": "video_url", "video_url": {"url": d64("video/mp4", MP4)}},
              {"type": "video", "video": "http://vid.test/../x.sh"},
              {"type": "input_video", "video_url": "http://vid.test/odd"},
              {"type": "input_audio", "input_audio": {"data": d64("audio/wav", WAV), "format": "wav"}},
              {"type": "input_audio", "input_audio": {"data": base64.b64encode(WAV).decode(), "format": "wav"}},
              {"type": "input_audio", "input_audio": {"data": "http://aud.test/a.wav", "format": "wav"}}])
    r = go(b, "chat", peer)
    parts = r["ok"]["messages"][0]["content"]
    files = sorted(os.listdir(r["dir"]))
    out[peer] = {"text": parts[0], "img_data": parts[1] == b["messages"][0]["content"][1],
                 "img_http": [parts[2]["image_url"]["url"][:22], parts[2]["image_url"]["detail"]],
                 "input_image": parts[3]["image_url"][:23],
                 "video_data": [parts[4]["video_url"]["url"].startswith(r["dir"]), parts[4]["video_url"]["url"][-4:]],
                 "video_http_ext": parts[5]["video"][-4:], "video_odd_ext": parts[6]["video_url"][-4:],
                 "audio_data_same": parts[7] == b["messages"][0]["content"][7],
                 "audio_raw_same": parts[8] == b["messages"][0]["content"][8],
                 "audio_http": parts[9]["input_audio"]["data"][:22],
                 "files": files, "file_modes": sorted({oct(stat.S_IMODE(os.stat(os.path.join(r["dir"], f)).st_mode)) for f in files}),
                 "dir_mode": oct(stat.S_IMODE(os.stat(r["dir"]).st_mode)),
                 "file_bytes": open(parts[4]["video_url"]["url"], "rb").read() == MP4,
                 "bytes": r["bytes"], "mutated": r["mutated"]}
    r["ticket"].close()
    out[peer]["dir_after_close"] = os.path.exists(r["dir"])
for name, part in (("path", {"type": "image_url", "image_url": {"url": "/Users/alex/a.png"}}),
                   ("file_url", {"type": "video", "video": "file:///Users/alex/a.mp4"}),
                   ("audio_ref", {"type": "input_audio", "input_audio": {"data": "/tmp/a.wav"}}),
                   ("audio_garbage", {"type": "input_audio", "input_audio": {"data": "not base64!!"}})):
    out["local_" + name] = [go(chat([part]), "chat", "LOOPBACK").get("err"),
                            go(chat([part]), "chat", "REMOTE").get("err")]
for name, part in (("a_file", {"type": "image", "source": {"type": "url", "url": "file:///etc/passwd"}}),
                   ("a_path", {"type": "image", "source": {"type": "url", "url": "/Users/alex/a.png"}}),
                   ("a_unknown", {"type": "image", "source": {"type": "path", "path": "/etc/passwd"}}),
                   ("a_nodict", {"type": "image", "source": "/etc/passwd"}),
                   ("c_audio_str", {"type": "input_audio", "input_audio": "/etc/passwd"}),
                   ("c_image_obj", {"type": "image_url", "image_url": {"detail": "high"}}),
                   ("c_image_list", {"type": "image_url", "image_url": ["/etc/passwd"]})):
    fam = "messages" if name.startswith("a_") else "chat"
    out["closed_" + name] = [go(chat([part]), fam, "LOOPBACK").get("err"),
                             go(chat([part]), fam, "REMOTE").get("err")]
out["file_id"] = go(chat([{"type": "input_image", "file_id": "file_1"}]), "chat", "LOOPBACK").get("err")
out["fetch_failed"] = go(chat([{"type": "image_url", "image_url": {"url": "http://denied.test/x.png"}}]),
                         "chat", "REMOTE").get("err")
CALLS.clear()
many = chat([{"type": "image_url", "image_url": {"url": "http://img.test/a.png"}}] * 3)
out["too_many"] = [go(many, "chat", "REMOTE", limits=dict(LIMITS, media_max_parts=2)).get("err"), len(CALLS)]
budget = M.Budget(10)
busy = go(chat([{"type": "image_url", "image_url": {"url": "http://img.test/a.png"}}]), "chat", "REMOTE", budget=budget)
busy["ticket"].close()
out["busy"] = [busy.get("err"), budget.used]
bad_root = os.path.join(root, "a_file"); open(bad_root, "w").write("x")
out["storage"] = go(chat([{"type": "video", "video": d64("video/mp4", MP4)}]), "chat", "LOOPBACK",
                    st=M.Store(os.path.join(bad_root, "media"))).get("err")
resp = {"model": "m", "input": [
    {"role": "user", "content": [{"type": "input_text", "text": "hi"},
                                 {"type": "input_image", "image_url": "http://img.test/a.png"}]},
    {"type": "function_call", "call_id": "c1", "name": "shot", "arguments": "{}"},
    {"type": "function_call_output", "call_id": "c1",
     "output": [{"type": "input_text", "text": "截图如下"}, {"type": "input_image", "image_url": "http://img.test/b.jpg"}]},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "好"}]}]}
r = go(resp, "responses", "REMOTE")
out["responses"] = {"img": r["ok"]["input"][0]["content"][1]["image_url"][:22],
                    "tool_img": r["ok"]["input"][2]["output"][1]["image_url"][:23],
                    "untouched": [r["ok"]["input"][i] == resp["input"][i] for i in (1, 3)] +
                                 [r["ok"]["input"][0]["content"][0] == resp["input"][0]["content"][0],
                                  r["ok"]["input"][2]["output"][0] == resp["input"][2]["output"][0]]}
msgs = {"model": "m", "messages": [
    {"role": "user", "content": [{"type": "text", "text": "hi"},
                                 {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                                 {"type": "image", "source": {"type": "url", "url": "http://img.test/b.jpg"}}]},
    {"role": "assistant", "content": [{"type": "thinking", "thinking": "嗯", "signature": "sig"},
                                      {"type": "tool_use", "id": "t1", "name": "shot", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": [{"type": "text", "text": "图"},
                                              {"type": "image", "source": {"type": "url", "url": "http://img.test/a.png"}}]}]}]}
r = go(msgs, "messages", "REMOTE")
m = r["ok"]["messages"]
out["messages"] = {"b64_same": m[0]["content"][1] == msgs["messages"][0]["content"][1],
                   "url": [m[0]["content"][2]["source"]["type"], m[0]["content"][2]["source"]["media_type"],
                           base64.b64decode(m[0]["content"][2]["source"]["data"]) == PNG],
                   "tool_result_img": m[2]["content"][0]["content"][1]["source"]["type"],
                   "assistant_same": m[1] == msgs["messages"][1],
                   "tool_text_same": m[2]["content"][0]["content"][0] == msgs["messages"][2]["content"][0]["content"][0]}
out["anthropic_file"] = go({"model": "m", "messages": [{"role": "user", "content": [
    {"type": "image", "source": {"type": "file", "file_id": "f"}}]}]}, "messages", "LOOPBACK").get("err")
pair = M.Budget(1 << 30)
ok = go(chat([{"type": "image_url", "image_url": {"url": "http://img.test/a.png"}}]), "chat", "REMOTE", budget=pair)
held = pair.used
ok["ticket"].close()
out["budget_pair"] = [held, pair.used]
CALLS.clear()
small = M.Budget(100 * 1024 * 1024)
pre = go(chat([{"type": "image_url", "image_url": {"url": "http://img.test/a.png"}}]), "chat", "REMOTE",
         budget=small)
out["prepay"] = [pre.get("err"), len(CALLS), small.used]
VID = chat([{"type": "video", "video": "http://vid.test/a.mp4"}])
vb = M.Budget(1 << 30)
v = go(VID, "chat", "REMOTE", budget=vb)
out["video_once"] = [vb.used, v["bytes"]]
v["ticket"].close()
# 同一类形态：Responses 任意 item 的 output / content，后端对四类工具输出类型都当图片加载
EVIL = {"type": "input_image", "image_url": "/etc/passwd"}
HTTP10 = {"type": "input_image", "image_url": "http://10.1.2.3/x.png"}
out["tool_types"] = {}
for t in TOOL_TYPES + ["totally_unknown_item"]:
    for key in ("output", "content"):
        item = {"type": t, key: [{"type": "input_text", "text": "x"}, EVIL]}
        out["tool_types"][t + "." + key] = [go({"model": "m", "input": [item]}, "responses", "LOOPBACK").get("err"),
                                            go({"model": "m", "input": [item]}, "responses", "REMOTE").get("err")]
CALLS.clear()
shell = {"model": "m", "input": [{"type": "shell_call_output", "output": [HTTP10]}]}
r = go(shell, "responses", "REMOTE")
out["shell_http"] = [r["ok"]["input"][0]["output"][0]["image_url"][:22], CALLS == ["http://10.1.2.3/x.png"]]
CALLS.clear()
cap = {"model": "m", "input": [{"type": "shell_call_output", "output": [HTTP10] * 3}]}
out["shell_cap"] = [go(cap, "responses", "REMOTE", limits=dict(LIMITS, media_max_parts=2)).get("err", [0])[0], len(CALLS)]
# 放行谓词与后端逐字一致：不 strip、区分大小写
strict = {}
for name, fam, part in (
        ("img_space", "chat", {"type": "image_url", "image_url": {"url": " data:image/png;base64,AAAA"}}),
        ("img_upper", "chat", {"type": "image_url", "image_url": {"url": "DATA:image/png;base64,AAAA"}}),
        ("img_nocomma", "chat", {"type": "image_url", "image_url": {"url": "data:image/png;base64"}}),
        ("aud_upper", "chat", {"type": "input_audio", "input_audio": {"data": "data:AUDIO/wav;base64,AAAA"}}),
        ("aud_b64upper", "chat", {"type": "input_audio", "input_audio": {"data": "data:audio/wav;BASE64,AAAA"}}),
        ("aud_text", "chat", {"type": "input_audio", "input_audio": {"data": "data:text/plain;base64,AAAA"}}),
        ("a_mt_text", "messages", {"type": "image", "source": {"type": "base64", "media_type": "text/x", "data": "AAAA"}}),
        ("a_mt_upper", "messages", {"type": "image", "source": {"type": "base64", "media_type": "Image/png", "data": "AAAA"}}),
        ("a_mt_int", "messages", {"type": "image", "source": {"type": "base64", "media_type": 5, "data": "AAAA"}}),
        ("a_nodata", "messages", {"type": "image", "source": {"type": "base64", "media_type": "image/png"}})):
    strict[name] = [go(chat([part]), fam, "LOOPBACK").get("err"), go(chat([part]), fam, "REMOTE").get("err")]
out["strict"] = strict
old = store.new_dir(); open(os.path.join(old, "1.mp4"), "wb").write(b"x")
fresh = store.new_dir(); open(os.path.join(fresh, "1.mp4"), "wb").write(b"x")
past = time.time() - 7200
os.utime(old, (past, past))
out["sweep"] = [store.sweep(), os.path.exists(old), os.path.exists(fresh)]
# 媒体根里一个指向别处、mtime 很旧的符号链接：清扫不得跟随它删目标目录里的文件
victim = tempfile.mkdtemp(); open(os.path.join(victim, "keep.txt"), "w").write("k")
link = os.path.join(store.root, "deadbeef")
os.symlink(victim, link)
os.utime(victim, (past, past)); os.utime(link, (past, past), follow_symlinks=False)
store.sweep()
out["sweep_symlink"] = os.path.exists(os.path.join(victim, "keep.txt"))
print(json.dumps(out, ensure_ascii=False))
"""


STATE_GLOB = "~/.local/share/mlx_server/.venv/lib/python3*/site-packages/mlx_vlm/server/responses_state.py"
FALLBACK_TOOL_TYPES = ["function_call_output", "shell_call_output", "apply_patch_call_output", "tool_result"]


def _deployed_tool_output_types():
    """从部署 venv 的 responses_state.py 抽「按工具输出处理」的 item 类型字面量；没部署返回 None。"""
    import glob
    import os
    import pathlib
    import re
    for path in sorted(glob.glob(os.path.expanduser(STATE_GLOB))):
        text = pathlib.Path(path).read_text(encoding="utf-8")
        m = re.search(r'item_type in \(\s*((?:"[a-z_]+",\s*)+)\)\s*:\s*\n\s*output = item\.get\("output"', text)
        assert m, "responses_state.py 里找不到工具输出类型元组：后端改版了，更新这条守卫"
        return re.findall(r'"([a-z_]+)"', m.group(1))
    return None


def test_deployed_backend_tool_output_types_are_scanned():
    import pytest
    deployed = _deployed_tool_output_types()
    if deployed is None:
        pytest.skip("本机没有已部署的 mlx_vlm（~/.local/share/mlx_server/.venv）：无从抽取后端的工具输出类型")
    assert len(deployed) >= 1, "抽取结果为空（test_media_processing 会拿这组类型逐个构造远端本地路径 item）"


def test_media_processing(payload, sys_py):
    types = _deployed_tool_output_types() or FALLBACK_TOOL_TYPES
    got = run_floor(payload, sys_py, PROG.replace("__TOOL_TYPES__", repr(list(types))), with_gateway=True, timeout=60)
    for peer in ("LOOPBACK", "REMOTE"):
        g = got[peer]
        assert g["text"] == {"type": "text", "text": "看图 /Users/alex/secret.png"}, "非媒体 part 必须原样"
        assert g["img_data"] is True, "data:image 透传"
        assert g["img_http"] == ["data:image/png;base64,", "high"], "http 图片 ⇒ data URI，同级字段保留"
        assert g["input_image"] == "data:image/jpeg;base64,"
        assert g["video_data"] == [True, ".mp4"], "data:video ⇒ 临时文件路径（mlx_vlm 不认 data: 视频）"
        assert (g["video_http_ext"], g["video_odd_ext"]) == (".mov", ".bin"), "扩展名只由 Content-Type 决定"
        assert g["audio_data_same"] and g["audio_raw_same"], "data:audio 与严格 base64 透传"
        assert g["audio_http"] == "data:audio/wav;base64,"
        assert g["files"] == ["1.mp4", "2.mov", "3.bin"]
        assert g["file_modes"] == ["0o600"] and g["dir_mode"] == "0o700" and g["file_bytes"]
        assert g["bytes"] == 104 * 2 + 88 * 3 + 64, \
            "代取（两张图 104、音频 64）与临时文件（三个视频 88）各计一次，代取来的视频不重复计"
        assert g["mutated"] is False, "不得修改入参"
        assert g["dir_after_close"] is False, "Ticket.close 必须删掉临时目录"
    rejected = [400, {"error": {"type": "media_source_rejected",
                                "message": "local paths and file:// references are only accepted from this machine",
                                "part": "messages[0].content[1]"}}]
    for name in ("path", "file_url", "audio_ref", "audio_garbage"):
        local, remote = got["local_" + name]
        assert local is None, f"{name}：本机来源应透传"
        assert remote == rejected, f"{name}：远端来源应 400 media_source_rejected，实得 {remote}"
    for name in ("a_file", "a_path", "a_unknown", "a_nodict", "c_audio_str", "c_image_obj", "c_image_list"):
        local, remote = got["closed_" + name]
        assert local is None, f"{name}：本机来源应放行"
        assert remote and remote[0] == 400 and remote[1]["error"]["type"] == "media_source_rejected", \
            f"{name}：未识别形态对远端必须 fail closed，实得 {remote}"
    assert got["file_id"][0] == 400 and got["file_id"][1]["error"]["type"] == "media_source_rejected"
    assert got["fetch_failed"] == [400, {"error": {"type": "media_fetch_failed",
                                                   "message": "could not fetch image for messages[0].content[1]",
                                                   "part": "messages[0].content[1]", "reason": "address_denied"}}]
    too_many, calls = got["too_many"]
    assert too_many[0] == 400 and too_many[1]["error"]["type"] == "too_many_media_parts" and calls == 0
    busy, used = got["busy"]
    assert busy[0] == 503 and busy[1]["error"]["type"] == "gateway_busy" and used == 0
    assert got["storage"][0] == 507 and got["storage"][1]["error"]["type"] == "media_storage_failed"
    assert got["responses"] == {"img": "data:image/png;base64,", "tool_img": "data:image/jpeg;base64,",
                                "untouched": [True, True, True, True]}
    assert got["messages"] == {"b64_same": True, "url": ["base64", "image/jpeg", True],
                               "tool_result_img": "base64", "assistant_same": True, "tool_text_same": True}
    assert got["anthropic_file"][0] == 400
    assert got["prepay"][0][0] == 503 and got["prepay"][0][1]["error"]["type"] == "gateway_busy" \
        and got["prepay"][1:] == [0, 0], "预算小于 max_bytes ⇒ 代取前就 503，fetcher 零调用"
    assert got["video_once"] == [88, 88], "代取视频只记一次实际字节（预留已退还差额）"
    for key, (local, remote) in got["tool_types"].items():
        assert local is None, f"{key}：本机来源应放行"
        assert remote and remote[0] == 400 and remote[1]["error"]["type"] == "media_source_rejected", \
            f"{key}：任何 item 的 output/content 里的本地路径对远端必须被拒，实得 {remote}"
    assert got["shell_http"] == ["data:image/png;base64,", True], "工具输出里的 http 图片也走代取，不绕过地址校验"
    assert got["shell_cap"] == [400, 0], "工具输出里的媒体 part 同样计入 media_max_parts"
    for key, (local, remote) in got["strict"].items():
        assert local is None, f"{key}：本机来源应放行"
        assert remote and remote[0] == 400 and remote[1]["error"]["type"] == "media_source_rejected", \
            f"{key}：网关放行谓词不得比后端宽，实得 {remote}"
    assert got["budget_pair"] == [104, 0], "reserve 与 release 必须成对：请求结束后在途字节归零"
    assert got["sweep"] == [1, False, True], "只清扫超过 1 小时的遗留目录"
    assert got["sweep_symlink"] is True, "清扫不跟随符号链接（lstat）"
