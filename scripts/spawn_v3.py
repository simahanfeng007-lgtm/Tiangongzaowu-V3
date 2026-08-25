#!/usr/bin/env python3
# 2026-08-25 add: spawn_v3 —— v3 桌面网关 HTTP API 封装（凌霜委托 cc）
# 用途：把 v3 当 spawn 子 agent 调用；纯标准库（urllib），无额外依赖。
# 用法：
#   python3 scripts/spawn_v3.py --prompt "你好" --session-id s1 --timeout 90
#   from scripts.spawn_v3 import SpawnV3; SpawnV3().ask("你好")["reply"]

"""SpawnV3：v3 桌面网关（/api/v1/gateway/desktop/*）最小封装。

- ask(prompt, session_id=None, timeout=120) -> {"generation", "status", "reply"}
  - 提交 inbound 后轮询 status，直到 run.status 进入 COMPLETED / FAILED。
  - FAILED 也正常返回（属于合法 run 终态，reply 里通常带失败原因）。
  - 超时 raise TimeoutError。
- HTTP 502/503/504 自动重试 1 次（间隔 1s），其余错误（4xx、连接失败等）直接 raise。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BASE_URL = "http://127.0.0.1:17173"
DEFAULT_TOKEN = "test-desktop-token-12345678901234567890"
RETRYABLE_STATUS = (502, 503, 504)
TERMINAL_STATUS = ("COMPLETED", "FAILED")


class SpawnV3Error(RuntimeError):
    """HTTP 非retryable错误 / 响应结构异常。"""


class SpawnV3:
    def __init__(self, base_url=None, token=None, poll_interval=2.0):
        self.base_url = (base_url or os.environ.get("TIANGONG_V3_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or os.environ.get("TIANGONG_V3_TOKEN") or DEFAULT_TOKEN
        self.poll_interval = poll_interval

    # ---- HTTP 基础：502/503/504 重试 1 次，其他错 raise ----
    def _http(self, method, path, query=None, body=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"X-Tiangong-Token": self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        last_err = None
        for attempt in (1, 2):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = SpawnV3Error(f"HTTP {e.code} {method} {path}: {e.read().decode('utf-8', 'replace')[:300]}")
                if e.code in RETRYABLE_STATUS and attempt == 1:
                    time.sleep(1.0)
                    continue
                raise last_err
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise SpawnV3Error(f"{type(e).__name__} {method} {path}: {e}") from e
        raise last_err  # 理论不可达

    # ---- 提交一轮对话，返回 gateway_request_id ----
    def submit(self, prompt, session_id=None):
        payload = {
            "presentation_request_id": "pr_spawn_v3_" + uuid.uuid4().hex[:12],
            "session_id": session_id or ("spawn_v3_s_" + uuid.uuid4().hex[:12]),
            "message_id": "msg_spawn_v3_" + uuid.uuid4().hex[:12],
            "text": prompt,
            "attachments": [],
            "submitted_at_ms": int(time.time() * 1000),
        }
        resp = self._http("POST", "/api/v1/gateway/desktop/inbound", body=payload)
        rid = resp.get("gateway_request_id")
        if not rid:
            raise SpawnV3Error(f"inbound 响应缺少 gateway_request_id: {resp}")
        return rid, payload["session_id"]

    # ---- 查一次状态 ----
    def status(self, request_id):
        resp = self._http("GET", "/api/v1/gateway/desktop/status", query={"request_id": request_id})
        run = resp.get("run") or {}
        return {
            "generation": run.get("generation"),
            "status": run.get("status"),
            "reply": run.get("final_response") or "",
            "steps": run.get("steps") or [],
        }

    # ---- 对外主入口：提交 + 轮询到终态 ----
    def ask(self, prompt, session_id=None, timeout=120):
        rid, sid = self.submit(prompt, session_id)
        deadline = time.monotonic() + timeout
        result = {"generation": None, "status": "PENDING", "reply": ""}
        while True:
            result = self.status(rid)
            if result["status"] in TERMINAL_STATUS:
                return {"generation": result["generation"], "status": result["status"], "reply": result["reply"]}
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"v3 run 未在 {timeout}s 内到终态: request_id={rid} session_id={sid} "
                    f"last_status={result['status']} generation={result['generation']}"
                )
            time.sleep(self.poll_interval)


def main():
    ap = argparse.ArgumentParser(description="把 v3 当 spawn 子 agent 调一次（单行 JSON 输出）")
    ap.add_argument("--prompt", required=True, help="发给 v3 的任务文本")
    ap.add_argument("--session-id", default=None, help="会话 id（续会话时传，默认新建")
    ap.add_argument("--timeout", type=float, default=120, help="等待终态的超时秒数（默认 120）")
    ap.add_argument("--base-url", default=None, help="v3 网关地址（默认 http://127.0.0.1:17173）")
    ap.add_argument("--token", default=None, help="X-Tiangong-Token（默认内置测试 token）")
    args = ap.parse_args()
    try:
        result = SpawnV3(base_url=args.base_url, token=args.token).ask(
            args.prompt, session_id=args.session_id, timeout=args.timeout
        )
    except Exception as e:  # CLI 层：错误也输出单行 JSON（stderr），退出码 1
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
