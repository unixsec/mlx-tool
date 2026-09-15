"""spec §4.2：规范化、白名单、扇出规则。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json, time
from gateway import endpoints as E
paths = ["/chat/completions", "/v1/chat/completions/", "/models", "/healthz",
         "/v1/responses/resp_1/input_items?limit=2&order=asc", "/"]
norm = {p: list(E.normalize(p)) for p in paths}
pairs = [("POST", "/v1/chat/completions"), ("GET", "/v1/chat/completions"),
         ("POST", "/v1/completions"), ("POST", "/v1/responses"),
         ("POST", "/v1/responses/input_tokens"), ("GET", "/v1/responses/resp_1"),
         ("DELETE", "/v1/responses/resp_1"), ("POST", "/v1/responses/resp_1/cancel"),
         ("GET", "/v1/responses/resp_1/input_items"), ("GET", "/v1/responses/.."),
         ("GET", "/v1/responses/a%2Fb"), ("POST", "/v1/messages"),
         ("POST", "/v1/messages/count_tokens"), ("GET", "/v1/models"), ("GET", "/healthz"),
         ("POST", "/v1/embeddings"), ("POST", "/v1/unload"), ("GET", "/v1/settings")]
match = {m + " " + p: E.match(m, p) for m, p in pairs}
def after(sec, status):
    def fn():
        time.sleep(sec)
        return None if status is None else (status, {"s": status})
    return fn
def run(spec, timeout=10.0):
    t0 = time.monotonic()
    st, body, sid = E.fanout([("m%d" % i, after(sec, s)) for i, (sec, s) in enumerate(spec)],
                             timeout_sec=timeout)
    return [st, sid, round(time.monotonic() - t0, 2)]
fan = {"404_200": run([(0, 404), (0.05, 200)]), "200_404": run([(0.05, 200), (0, 404)]),
       "all404": run([(0, 404), (0, 404)]), "500_404": run([(0, 500), (0, 404)]),
       "none_404": run([(0, None), (0, 404)]), "400_404": run([(0, 400), (0, 404)]),
       "slow": run([(3, 200), (0, 404)], timeout=0.5), "parallel": run([(1, 404), (1, 200)])}
print(json.dumps({"norm": norm, "match": match, "fan": fan,
                  "empty": list(E.fanout([], timeout_sec=1)),
                  "legacy": list(E.endpoints_of({"model_arg": "x"})),
                  "explicit": list(E.endpoints_of({"endpoints": ["chat", "responses"]})),
                  # Important 2：backends.tsv 的 `endpoints` 列写 `-` 或留空，
                  # write_record 把两者都规范成空列表；endpoints_of 收到空列表时
                  # 要跟「键缺失」一样回退到 LEGACY_ENDPOINTS，不能当成「零端点」。
                  "absent_key": list(E.endpoints_of({"model_arg": "x"})),
                  "present_empty_list": list(E.endpoints_of({"endpoints": []}))}))
"""


def test_normalize_match_and_fanout(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    assert got["norm"] == {
        "/chat/completions": ["/v1/chat/completions", ""],
        "/v1/chat/completions/": ["/v1/chat/completions", ""],
        "/models": ["/v1/models", ""], "/healthz": ["/healthz", ""],
        "/v1/responses/resp_1/input_items?limit=2&order=asc":
            ["/v1/responses/resp_1/input_items", "limit=2&order=asc"],
        "/": ["/v1/", ""]}
    B, F = "body", "fanout"
    assert got["match"] == {
        "POST /v1/chat/completions": ["chat", B], "GET /v1/chat/completions": None,
        "POST /v1/completions": ["completions", B], "POST /v1/responses": ["responses", B],
        "POST /v1/responses/input_tokens": ["responses", B],
        "GET /v1/responses/resp_1": ["responses", F], "DELETE /v1/responses/resp_1": ["responses", F],
        "POST /v1/responses/resp_1/cancel": ["responses", F],
        "GET /v1/responses/resp_1/input_items": ["responses", F],
        "GET /v1/responses/..": None, "GET /v1/responses/a%2Fb": None,
        "POST /v1/messages": ["messages", B], "POST /v1/messages/count_tokens": ["messages", B],
        "GET /v1/models": [None, "local"], "GET /healthz": [None, "local"],
        "POST /v1/embeddings": None, "POST /v1/unload": None, "GET /v1/settings": None}
    f = got["fan"]
    assert f["404_200"][:2] == [200, "m1"] and f["200_404"][:2] == [200, "m0"]
    assert f["all404"][:2] == [404, None]
    assert f["500_404"][:2] == [502, None] and f["none_404"][:2] == [502, None]
    assert f["400_404"][:2] == [400, "m0"]
    assert f["slow"][:2] == [504, None] and f["slow"][2] < 1.0, "单后端超时要按期返回 504"
    assert f["parallel"][:2] == [200, "m1"] and f["parallel"][2] < 1.5, "扇出必须并发"
    assert got["empty"] == [404, None, None]
    assert got["legacy"] == ["chat", "completions"] and got["explicit"] == ["chat", "responses"]
    assert got["absent_key"] == ["chat", "completions"]
    assert got["present_empty_list"] == ["chat", "completions"], \
        "`endpoints` 键在但是空列表（`-`/空 cell 规范化后的样子）要回退到 LEGACY_ENDPOINTS"
