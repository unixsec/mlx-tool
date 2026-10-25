"""T2 · 媒体处理（spec §4.4、D8）。**只动媒体 part**，其余一律原样。

mlx_vlm 对媒体来源的处理是「data URI 解码、http(s) 自己外连、其余当本机路径」：
远端来源发来的 http URL 会让后端替它对外连接（SSRF），发来的本机路径会让后端读本机文件。
网关据此分档：http(s) 一律由网关代取（`fetch.py` 校验目标地址）后改写成 data URI；
本机路径与 `file://` 只对本机来源放行；视频后端不认 `data:`，解码或代取后落临时文件再给路径。
"""
import base64
import binascii
import contextlib
import copy
import os
import threading
import time
import uuid

from . import fetch as _fetch

SWEEP_AGE_SEC = 3600.0
AUDIO_REF_PREFIXES = ("http://", "https://", "file://", "/", "./", "../")   # 与 mlx_vlm 相同
AUDIO_REF_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm")
VIDEO_EXT = {"mp4": "mp4", "webm": "webm", "quicktime": "mov", "x-matroska": "mkv",
             "matroska": "mkv", "x-msvideo": "avi", "avi": "avi"}
_CHAT_MEDIA = ("image_url", "input_image", "input_audio", "video_url", "video", "input_video")
_LOCAL_MSG = "local paths and file:// references are only accepted from this machine"
_SAME = object()


class MediaError(Exception):
    """媒体处理失败。`reply()` 给出回客户端的 `(status, body)`。"""

    def __init__(self, status, err_type, message, part=None, reason=None):
        # type: (int, str, str, object, object) -> None
        super().__init__(message)
        self.status, self.err_type, self.message = status, err_type, message
        self.part, self.reason = part, reason

    def reply(self):
        # type: () -> tuple
        err = {"type": self.err_type, "message": self.message}
        if self.part is not None:
            err["part"] = self.part
        if self.reason is not None:
            err["reason"] = self.reason
        return (self.status, {"error": err})


class Budget:
    """全网关在途字节（请求体 + 代取 + 临时文件）。**线程安全**。"""

    def __init__(self, limit_bytes):
        # type: (int) -> None
        self.limit = int(limit_bytes)
        self.used = 0
        self._lock = threading.Lock()

    def reserve(self, n):
        # type: (int) -> bool
        with self._lock:
            if self.used + n > self.limit:
                return False
            self.used += n
            return True

    def release(self, n):
        # type: (int) -> None
        with self._lock:
            self.used = max(0, self.used - n)


class Store:
    """`<数据根>/run/media/<uuid4>/<序号>.<扩展名>`。**目录扁平，不用 rmtree**：逐个删文件再删目录。"""

    def __init__(self, root):
        # type: (str) -> None
        self.root = root

    def new_dir(self):
        # type: () -> str
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        path = os.path.join(self.root, uuid.uuid4().hex)
        os.mkdir(path, 0o700)
        return path

    def write(self, directory, index, content_type, data):
        # type: (str, int, str, bytes) -> str
        subtype = content_type.split("/", 1)[-1].split(";")[0].strip().lower()
        path = os.path.join(directory, f"{index}.{VIDEO_EXT.get(subtype, 'bin')}")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path

    @staticmethod
    def remove_dir(directory):
        # type: (str) -> None
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            return
        for name in names:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(os.path.join(directory, name))
        with contextlib.suppress(FileNotFoundError):
            os.rmdir(directory)

    def sweep(self, now=None, age_sec=SWEEP_AGE_SEC):
        # type: (object, float) -> int
        """清扫遗留目录（网关崩溃时 finally 没跑到）。网关启动与路由刷新时调。"""
        now = time.time() if now is None else now
        removed = 0
        try:
            names = os.listdir(self.root)
        except FileNotFoundError:
            return 0
        for name in names:
            path = os.path.join(self.root, name)
            try:
                if os.path.isdir(path) and now - os.stat(path).st_mtime > age_sec:
                    self.remove_dir(path)
                    removed += 1
            except OSError:
                continue
        return removed


class Ticket:
    """一次请求占用的在途字节与临时目录。**在请求处理的 `finally` 里 `close()`**。"""

    def __init__(self, budget, store):
        # type: (Budget, Store) -> None
        self.budget, self.store = budget, store
        self.bytes = 0
        self.dir = None

    def charge(self, n):
        # type: (int) -> None
        if not self.budget.reserve(n):
            raise MediaError(503, "gateway_busy", "gateway is busy with other media, retry shortly")
        self.bytes += n

    def refund(self, n):
        # type: (int) -> None
        n = min(n, self.bytes)
        self.budget.release(n)
        self.bytes -= n

    def close(self):
        # type: () -> None
        self.budget.release(self.bytes)
        self.bytes = 0
        if self.dir:
            Store.remove_dir(self.dir)
            self.dir = None


def _slots(body, family):
    # type: (dict, str) -> list
    """媒体槽位 `(part 路径, 形态, 容器, 键, 子键)`。**只收媒体类 part**。"""
    slots = []

    def chat_part(path, part):
        kind = part.get("type")
        if kind == "image_url":
            sub = "url" if isinstance(part.get("image_url"), dict) else None
            slots.append((path, "image", part, "image_url", sub))
        elif kind == "input_image":
            sub = "url" if isinstance(part.get("image_url"), dict) else None
            slots.append((path, "input_image", part, "image_url", sub))
        elif kind == "input_audio":
            if isinstance(part.get("input_audio"), dict):
                slots.append((path, "audio", part["input_audio"], "data", None))
            else:
                slots.append((path, "audio_bad", part, "input_audio", None))
        elif kind in ("video_url", "video", "input_video"):
            key = {"video_url": "video_url", "video": "video"}.get(
                kind, "video" if part.get("video") else "video_url")
            sub = "url" if isinstance(part.get(key), dict) else None
            slots.append((path, "video", part, key, sub))

    def content_list(prefix, content):
        for j, part in enumerate(content if isinstance(content, list) else []):
            if isinstance(part, dict) and part.get("type") in _CHAT_MEDIA:
                chat_part(f"{prefix}[{j}]", part)

    if family in ("chat", "completions"):
        for i, msg in enumerate(body.get("messages") or []):
            if isinstance(msg, dict):
                content_list(f"messages[{i}].content", msg.get("content"))
    elif family == "responses":
        items = body.get("input")
        for i, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            if item.get("type") in _CHAT_MEDIA:
                chat_part(f"input[{i}]", item)
            # 后端对 function_call_output / shell_call_output / apply_patch_call_output / tool_result
            # 都取 output 或 content 里的部件当图片加载：按类扫描，不按 item.type 白名单
            content_list(f"input[{i}].output", item.get("output"))
            content_list(f"input[{i}].content", item.get("content"))
    elif family == "messages":
        for i, msg in enumerate(body.get("messages") or []):
            content = msg.get("content") if isinstance(msg, dict) else None
            for j, part in enumerate(content if isinstance(content, list) else []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image":
                    slots.append((f"messages[{i}].content[{j}]", "anthropic", part, "source", None))
                elif part.get("type") == "tool_result" and isinstance(part.get("content"), list):
                    for k, sub in enumerate(part["content"]):
                        if isinstance(sub, dict) and sub.get("type") == "image":
                            slots.append((f"messages[{i}].content[{j}].content[{k}]",
                                          "anthropic", sub, "source", None))
    return slots


def _is_http(value):
    # type: (object) -> bool
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def _data_uri(value, major):
    # type: (object, str) -> object
    """→ `(media_type, base64 串)`，不是该大类的 base64 data URI 则 None。"""
    if not isinstance(value, str) or not value.strip().lower().startswith("data:"):
        return None
    header, sep, payload = value.strip().partition(",")
    media_type = header[5:].split(";")[0].strip().lower()
    if not sep or ";base64" not in header.lower() or not media_type.startswith(major + "/"):
        return None
    return media_type, payload


class _Run:
    def __init__(self, peer_class, allowed_nets, limits, ticket, store, fetcher):
        self.peer_class, self.allowed_nets = peer_class, allowed_nets
        self.ticket, self.store = ticket, store
        self.fetcher = fetcher or _fetch.fetch
        self.max_bytes = int(limits.get("media_max_mb", 128)) * 1024 * 1024
        self.index = 0

    def fetch(self, url, major, part):
        """代取前先按上限预留在途字节（否则并发 N×上限都不在账上），取完退还差额，只留实际字节。"""
        self.ticket.charge(self.max_bytes)
        try:
            got = self.fetcher(url, self.peer_class, self.allowed_nets, self.max_bytes, (major,))
        except _fetch.FetchError as exc:
            self.ticket.refund(self.max_bytes)
            # detail 可能带路径或带签名的查询串：只取 reason，且 from None 切断异常链，免得 traceback 把它带进日志
            raise MediaError(400, "media_fetch_failed", f"could not fetch {major} for {part}",
                             part=part, reason=exc.reason) from None
        except BaseException:
            self.ticket.refund(self.max_bytes)
            raise
        self.ticket.refund(max(0, self.max_bytes - len(got["data"])))
        return got

    def local(self, part):
        if self.peer_class != "LOOPBACK":
            raise MediaError(400, "media_source_rejected", _LOCAL_MSG, part=part)
        return _SAME

    def temp_file(self, content_type, data, part, charge=True):
        if charge:
            self.ticket.charge(len(data))
        try:
            if self.ticket.dir is None:
                self.ticket.dir = self.store.new_dir()
            self.index += 1
            return self.store.write(self.ticket.dir, self.index, content_type, data)
        except OSError:
            raise MediaError(507, "media_storage_failed", "could not store media on disk",
                             part=part) from None


def _resolve(run, kind, value, part, holder):
    # type: (_Run, str, object, str, dict) -> object
    if kind == "anthropic":
        source = value if isinstance(value, dict) else {}
        if source.get("type") == "file":
            raise MediaError(400, "media_source_rejected", "file sources are not supported", part=part)
        if source.get("type") == "base64":
            media_type, data = source.get("media_type"), source.get("data")
            # 后端拼成 data:{media_type};base64,{data}，media_type 不以 image/ 开头就当路径读
            if (not media_type or (isinstance(media_type, str) and media_type.startswith("image/"))) \
                    and isinstance(data, str) and data:
                return _SAME
            return run.local(part)
        if source.get("type") == "url" and _is_http(source.get("url")):
            got = run.fetch(source["url"], "image", part)
            return {"type": "base64", "media_type": got["content_type"],
                    "data": base64.b64encode(got["data"]).decode("ascii")}
        return run.local(part)                             # 其它 url 形态、未知 type、非 dict：fail closed
    if kind == "input_image" and holder.get("file_id") is not None:
        raise MediaError(400, "media_source_rejected", "file_id is not supported", part=part)
    if kind == "audio_bad":
        return run.local(part)                             # input_audio 不是对象：后端行为不明，fail closed
    if not isinstance(value, str) or not value.strip():
        return run.local(part)                             # 取不到字符串来源：未识别形态不默认放行
    if kind in ("image", "input_image"):
        if value.startswith("data:image/") and "," in value:   # 后端逐字谓词：区分大小写，不去空白
            return _SAME
        if _is_http(value):
            got = run.fetch(value, "image", part)
            return f"data:{got['content_type']};base64," + base64.b64encode(got["data"]).decode("ascii")
        return run.local(part)
    if kind == "audio":
        stripped = value.strip()
        if stripped.startswith("data:"):
            prefix, sep, _ = stripped.partition(",")
            if sep and ";base64" in prefix and prefix.startswith("data:audio/"):   # 后端逐字谓词
                return _SAME
            return run.local(part)                         # 其它 data: 形态后端当路径
        if _is_http(value):
            got = run.fetch(value, "audio", part)
            return f"data:{got['content_type']};base64," + base64.b64encode(got["data"]).decode("ascii")
        if stripped.startswith(AUDIO_REF_PREFIXES) or stripped.lower().endswith(AUDIO_REF_SUFFIXES):
            return run.local(part)
        try:
            base64.b64decode(stripped, validate=True)
            return _SAME                                   # 严格 base64 ⇒ 后端当音频数据解
        except (binascii.Error, ValueError):
            return run.local(part)                         # 解不开 ⇒ 后端会把它当路径
    if kind == "video":
        data_uri = _data_uri(value, "video")
        if data_uri:
            try:
                data = base64.b64decode(data_uri[1], validate=True)
            except (binascii.Error, ValueError):
                raise MediaError(400, "media_source_rejected", "data:video is not valid base64",
                                 part=part) from None
            return run.temp_file(data_uri[0], data, part)
        if _is_http(value):
            # 代取时已按实际字节记账；落盘不再记第二次（否则预算提前一倍触发）
            got = run.fetch(value, "video", part)
            return run.temp_file(got["content_type"], got["data"], part, charge=False)
        return run.local(part)
    return _SAME


def process(body, family, peer_class, allowed_nets, limits, ticket, store, fetcher=None):
    # type: (dict, str, str, list, dict, Ticket, Store, object) -> dict
    """→ 新请求体。**先数槽位**：超过 `media_max_parts` 立即 400，不做任何代取。"""
    out = copy.deepcopy(body or {})
    slots = _slots(out, family)
    cap = int(limits.get("media_max_parts", 32))
    if len(slots) > cap:
        raise MediaError(400, "too_many_media_parts",
                         f"request has {len(slots)} media parts, the limit is {cap}")
    run = _Run(peer_class, allowed_nets, limits, ticket, store, fetcher)
    for path, kind, holder, key, sub in slots:
        raw = holder.get(key)
        value = raw.get(sub) if sub and isinstance(raw, dict) else raw
        new = _resolve(run, kind, value, path, holder)
        if new is _SAME:
            continue
        if sub and isinstance(raw, dict):
            holder[key] = dict(raw, **{sub: new})
        else:
            holder[key] = new
    return out
