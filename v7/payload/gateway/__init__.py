"""T2 · 网关层（venv Python，可用 fastapi/httpx）。

**外部应用接入的唯一面**：配置 base url + model id + api key 三项即可用。
它只经**四符号只读门面**调 T1（`G-DEP-03` 相等断言）：
`store.load` / `supervisor.running` / `supervisor.port_owner` / `supervisor.snapshot`。
"""
