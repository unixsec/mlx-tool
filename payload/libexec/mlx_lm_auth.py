"""mlx_lm 后端的鉴权包装：给上游 `mlx_lm.server` 的请求处理器加一道 Bearer 校验，然后照常启动。

上游没有鉴权机制，端口只绑 127.0.0.1 —— 本机任何进程（含同机其他用户）都能绕过网关令牌直接调用。
mlx_vlm 有 `MLX_VLM_SERVER_API_KEY`，这里给 mlx_lm 补上对等的一道。

**结构对不上就拒绝启动**（退出码 3），不在无鉴权下静默起来：上游改了类名或方法，
补丁没打上却照常服务，比起不来更糟。密钥从环境变量读取后立即移出环境。
"""
import hmac
import os
import sys

KEY_ENV = "MLX_LM_SERVER_API_KEY"
_METHODS = ("do_GET", "do_POST", "do_OPTIONS")


def _guard(fn, want):
    def wrapped(self):
        got = (self.headers.get("Authorization") or "").encode("utf-8", "replace")
        if not hmac.compare_digest(got, want):
            body = b'{"error": {"message": "unauthorized", "type": "authentication_error"}}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        return fn(self)
    wrapped.__wrapped__ = fn
    return wrapped


def install(server_module, key):
    # type: (object, str) -> object
    """把校验装到 `server_module.APIHandler` 上。→ None 成功 · 字符串 = 结构不符的原因。"""
    handler = getattr(server_module, "APIHandler", None)
    if handler is None or not callable(getattr(server_module, "main", None)):
        return "mlx_lm.server 里找不到 APIHandler / main"
    missing = [m for m in _METHODS if not callable(getattr(handler, m, None))]
    if missing:
        return "APIHandler 缺少 " + "、".join(missing)
    want = ("Bearer " + key).encode("utf-8")
    for m in _METHODS:
        setattr(handler, m, _guard(getattr(handler, m), want))
    return None


def main():
    key = os.environ.pop(KEY_ENV, "")
    if not key:
        sys.stderr.write(f"{KEY_ENV} 为空：mlx_lm 后端拒绝在无鉴权下启动\n")
        return 6
    from mlx_lm import server
    bad = install(server, key)
    if bad:
        sys.stderr.write(f"鉴权包装与上游结构不符（{bad}），拒绝在无鉴权下启动\n")
        return 3
    sys.argv[0] = "mlx_lm.server"
    server.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
