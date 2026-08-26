#!/usr/bin/env python3
# 2026-08-26 add: tiangong_shell —— v3 起源版 customtkinter 桌面壳骨架（凌霜委托 Kimi）
"""天工造物 v3 桌面壳：读 tiangong-launcher.ini → 注入环境变量 → 起 pythonw 子进程 → HTTP 通信。

- 纯标准库 + customtkinter；HTTP 协议复用 scripts/spawn_v3.py 的 desktop inbound/status 端点。
- ini 由 NSIS 安装器写入（token + 路径），与本文件同级目录。
"""

from __future__ import annotations

import configparser
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

try:  # customtkinter 仅在真起 UI 时需要；单测只测 ini 解析 + 启动参数构造，可缺失
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None

INI_NAME = "tiangong-launcher.ini"
DEFAULT_PORT = 17173
HEALTH_PATH = "/api/v1/v3/life/health"
TERMINAL_STATUS = ("COMPLETED", "FAILED")

# ini [gateway] 键 → 子进程环境变量（名称对齐 src/total_gateway/bootstrap.py）
INI_ENV_MAP = {
    "desktop_token": "TIANGONG_DESKTOP_TOKEN",
    "backend_token": "TIANGONG_BACKEND_INTERNAL_TOKEN",
    "life_token": "TIANGONG_LIFE_INTERNAL_TOKEN",
    "communication_token": "TIANGONG_GATEWAY_COMMUNICATION_TOKEN",
    "artifact_token": "TIANGONG_GATEWAY_LIFE_INTENT_TOKEN",
    "workspace_root": "TIANGONG_GATEWAY_WORKSPACE_ROOT",
    "release_source_root": "TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT",
}
FIXED_ENV = {
    "TIANGONG_GATEWAY_ENVIRONMENT": "test",  # 仅 test 允许非 7184 端口（bootstrap.py 校验）
    "TIANGONG_GATEWAY_DEPLOYMENT_MODE": "embedded",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class LauncherError(RuntimeError):
    """配置 / 端口 / 运行时缺失等启动期错误。"""


def log(message):
    print(f"[tiangong-shell] {message}", file=sys.stderr, flush=True)


# ---------- ini 读取 / 端口 ----------

def load_launcher_config(ini_path):
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        raise LauncherError(f"缺少启动配置: {ini_path}")
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path, encoding="utf-8")
    except configparser.Error as exc:
        raise LauncherError(f"启动配置解析失败: {exc}") from exc
    if not parser.has_section("gateway"):
        raise LauncherError("启动配置缺少 [gateway] 段")
    section = parser["gateway"]
    config = {key: section.get(key, "").strip() for key in INI_ENV_MAP}
    raw_port = section.get("port", "").strip()
    try:
        config["port"] = int(raw_port) if raw_port else DEFAULT_PORT
    except ValueError as exc:
        raise LauncherError(f"[gateway] port 非法: {raw_port!r}") from exc
    if not 1 <= config["port"] <= 65535:
        raise LauncherError(f"[gateway] port 超出范围: {config['port']}")
    if not config["desktop_token"]:
        raise LauncherError("[gateway] desktop_token 为空")
    return config


def find_available_port(preferred):
    """端口被占 → 动态换端口（bind 0 让系统分配）。"""
    for candidate in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
        except OSError:
            continue
    raise LauncherError("无可用端口")


def write_back_port(ini_path, port):
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path, encoding="utf-8")
        if not parser.has_section("gateway"):
            parser.add_section("gateway")
        parser.set("gateway", "port", str(port))
        with open(ini_path, "w", encoding="utf-8") as handle:
            parser.write(handle)
    except OSError as exc:
        raise LauncherError(f"端口写回 ini 失败: {exc}") from exc


# ---------- 子进程启动参数 ----------

def build_child_env(config, base_env=None):
    env = dict(os.environ if base_env is None else base_env)
    env.update(FIXED_ENV)
    env["TIANGONG_GATEWAY_PORT"] = str(config["port"])
    for ini_key, env_name in INI_ENV_MAP.items():
        value = config.get(ini_key, "")
        if value:
            env[env_name] = value
    return env


def build_child_command(app_root):
    runtime = Path(app_root) / "runtime" / "python312"
    for name in ("pythonw.exe", "python.exe"):
        candidate = runtime / name
        if candidate.is_file():
            return [str(candidate), "-m", "total_gateway"]
    raise LauncherError(f"内嵌运行时缺失: {runtime}")


# ---------- HTTP（spawn_v3 协议） ----------

def http_json(method, url, token, body=None, timeout=15):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"X-Tiangong-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def probe_health(base_url, token):
    """GET /api/v1/v3/life/health；任何异常一律 False，不抛给 UI。"""
    try:
        http_json("GET", base_url + HEALTH_PATH, token, timeout=3)
        return True
    except Exception:
        return False


def submit_prompt(base_url, token, text, session_id):
    payload = {
        "presentation_request_id": "pr_shell_" + uuid.uuid4().hex[:12],
        "session_id": session_id,
        "message_id": "msg_shell_" + uuid.uuid4().hex[:12],
        "text": text,
        "attachments": [],
        "submitted_at_ms": int(time.time() * 1000),
    }
    resp = http_json("POST", base_url + "/api/v1/gateway/desktop/inbound", token, body=payload)
    request_id = resp.get("gateway_request_id")
    if not request_id:
        raise LauncherError(f"inbound 响应缺少 gateway_request_id: {resp}")
    return request_id


def poll_status(base_url, token, request_id):
    resp = http_json(
        "GET", f"{base_url}/api/v1/gateway/desktop/status?request_id={request_id}", token
    )
    # bug-fix: 返回完整 run —— FAILED 时 UI 还要读 error_detail.message/.action 做中文指引（2026-08-26，凌霜修 UX）
    return resp.get("run") or {}


def cancel_run(base_url, token, request_id):
    # bug-fix: 中断必须取消服务端 generation —— 只停本地轮询时 run 仍 ACTIVE，
    # 同 session 后续消息会一直排队转圈；协议对齐 desktop_api run/control（2026-08-26，凌霜修 UX）
    return http_json(
        "POST", base_url + "/api/v1/run/control", token,
        body={"action": "cancel", "request_id": request_id}, timeout=5,
    )


# ---------- UI ----------

def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def _attach_tooltip(widget, text):
    # bug-fix: 中断按钮补悬停提示 —— customtkinter 无内置 tooltip，用 Toplevel 实现（2026-08-26，凌霜修 UX）
    import tkinter as tk

    tip = None

    def show(_event):
        nonlocal tip
        if tip is not None:
            return
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{widget.winfo_rootx() + 12}+{widget.winfo_rooty() + 36}")
        tk.Label(tip, text=text, background="#2b2b2b", foreground="#eeeeee",
                 padx=6, pady=3).pack()

    def hide(_event):
        nonlocal tip
        if tip is not None:
            tip.destroy()
            tip = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


class ShellApp:
    def __init__(self, config, command, env, cwd=None):
        self.config = config
        self.command = command
        self.env = env
        # bug-fix: 子进程 cwd 落 backend 目录 —— ._pth 已追加 backend 条目，这里再兜底
        # 一层（pythonw -m total_gateway 的相对布局解析）（2026-08-26，凌霜修 UX）
        self.cwd = cwd
        self.base_url = f"http://127.0.0.1:{config['port']}"
        self.token = config["desktop_token"]
        self.session_id = "shell_s_" + uuid.uuid4().hex[:12]
        self.proc = None
        self.cancel_event = threading.Event()
        # bug-fix: 记录在途 request_id —— on_interrupt 需要它取消服务端 generation（2026-08-26，凌霜修 UX）
        self.active_request_id = None
        self.log_queue = queue.Queue()
        self.healthy = False
        self.base_url = f"http://127.0.0.1:{config['port']}"
        self.token = config["desktop_token"]
        self.session_id = "shell_s_" + uuid.uuid4().hex[:12]
        self.proc = None
        self.cancel_event = threading.Event()
        self.log_queue = queue.Queue()
        self.healthy = False

        ctk.set_widget_scaling(1.2)
        self.root = ctk.CTk()
        self.root.title("天工造物 v3")
        self.root.geometry("720x520")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        top = ctk.CTkFrame(self.root)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(top, text="天工造物 v3", font=("", 18, "bold")).pack(side="left", padx=8)
        self.light = ctk.CTkLabel(top, text="●", text_color="grey", font=("", 18))
        self.light.pack(side="right", padx=8)

        self.chat = ctk.CTkTextbox(self.root, state="disabled", wrap="word")
        self.chat.pack(fill="both", expand=True, padx=10, pady=4)

        bar = ctk.CTkFrame(self.root)
        bar.pack(fill="x", padx=10, pady=4)
        self.entry = ctk.CTkEntry(bar, placeholder_text="输入消息…")
        self.entry.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.entry.bind("<Return>", lambda _e: self.on_send())
        ctk.CTkButton(bar, text="发送", width=72, command=self.on_send).pack(side="left")
        # bug-fix: 中断从 10×10 无字色块（命中区太小点不中）改为文本按钮，
        # 宽度对齐"发送"，另加悬停提示（2026-08-26，凌霜修 UX）
        interrupt_btn = ctk.CTkButton(
            bar, text="中断", width=72,
            fg_color="#c0392b", hover_color="#e74c3c", command=self.on_interrupt,
        )
        interrupt_btn.pack(side="left", padx=6)
        _attach_tooltip(interrupt_btn, "停止当前正在生成的回复")

        self.drawer_open = False
        ctk.CTkButton(self.root, text="日志 ▾", width=80, command=self.toggle_drawer).pack(
            anchor="w", padx=10
        )
        self.drawer = ctk.CTkTextbox(self.root, height=120, state="disabled", wrap="none")

        self.root.after(200, self._drain_logs)

    # ---- 子进程 + 健康探测（后台线程，不阻塞 UI） ----
    def start_backend(self):
        try:
            self.proc = subprocess.Popen(
                self.command, env=self.env, cwd=self.cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            self._fail(f"启动失败: {exc}")
            return
        threading.Thread(target=self._stderr_reader, daemon=True).start()
        threading.Thread(target=self._probe_loop, daemon=True).start()

    def _stderr_reader(self):
        try:
            for line in self.proc.stderr:
                self.log_queue.put(line.rstrip())
        except (OSError, ValueError):
            pass

    def _probe_loop(self):
        deadline = time.monotonic() + 30  # 探测 30s 超时
        while time.monotonic() < deadline:
            rc = self.proc.poll()
            if rc is not None:  # 子进程没起来（含 5s 内退出）
                self._fail(f"启动失败，子进程退出码 {rc}，见日志抽屉")
                return
            if probe_health(self.base_url, self.token):
                self.healthy = True
                self.root.after(0, lambda: self.light.configure(text_color="#2ecc71"))
                self._append_chat("系统", "后端已就绪。")
                return
            time.sleep(2)
        self._fail("后端未响应（30s 超时），见日志抽屉")

    def _fail(self, message):
        log(message)
        def show():
            self.light.configure(text_color="#e74c3c")
            self._open_drawer()
            try:
                from tkinter import messagebox

                messagebox.showerror("天工造物 v3", message)
            except Exception:
                pass
        self.root.after(0, show)

    # ---- 对话 ----
    def on_send(self):
        text = self.entry.get().strip()
        if not text or not self.healthy:
            return
        self.entry.delete(0, "end")
        self.cancel_event.clear()
        self._append_chat("我", text)
        threading.Thread(target=self._ask_worker, args=(text,), daemon=True).start()

    def _ask_worker(self, text):
        try:
            request_id = submit_prompt(self.base_url, self.token, text, self.session_id)
            self.active_request_id = request_id
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if self.cancel_event.is_set():
                    self._append_chat("系统", "（已中断）")
                    return
                run = poll_status(self.base_url, self.token, request_id)
                status = run.get("status")
                if status in TERMINAL_STATUS:
                    self._append_chat("天工", self._format_reply(status, run))
                    return
                time.sleep(2)
            self._append_chat("系统", "（等待终态超时）")
        except Exception as exc:
            self._append_chat("系统", f"（错误: {exc}）")
        finally:
            self.active_request_id = None

    @staticmethod
    def _format_reply(status, run):
        # bug-fix: FAILED 时读 desktop_api 已准备的中文 error_detail.message + .action 展示，
        # 不再只甩一个 [FAILED]（2026-08-26，凌霜修 UX）
        reply = (run.get("final_response") or "").strip()
        if status == "FAILED":
            detail = run.get("error_detail") or {}
            message = str(detail.get("message") or "").strip()
            action = str(detail.get("action") or "").strip()
            text = f"[FAILED] {message}" if message else "[FAILED]"
            if action:
                text += f"\n（{action}）"
            return (reply + "\n" if reply else "") + text
        return reply or f"[{status}]"

    def on_interrupt(self):
        # bug-fix: 只 set cancel_event 停的是本地轮询，服务端 generation 仍 ACTIVE ——
        # 补发 /api/v1/run/control cancel（后台线程，不卡 UI；失败提示不吞）（2026-08-26，凌霜修 UX）
        self.cancel_event.set()
        request_id = self.active_request_id
        if not request_id:
            return

        def _cancel():
            try:
                cancel_run(self.base_url, self.token, request_id)
            except Exception as exc:
                log(f"服务端取消失败: {exc}")
                self._append_chat("系统", f"（中断服务端任务失败: {exc}）")

        threading.Thread(target=_cancel, daemon=True).start()

    # ---- UI 小件 ----
    def _append_chat(self, who, text):
        def show():
            self.chat.configure(state="normal")
            self.chat.insert("end", f"{who}: {text}\n\n")
            self.chat.see("end")
            self.chat.configure(state="disabled")
        self.root.after(0, show)

    def _open_drawer(self):
        if not self.drawer_open:
            self.drawer_open = True
            self.drawer.pack(fill="x", padx=10, pady=(0, 10))

    def toggle_drawer(self):
        if self.drawer_open:
            self.drawer_open = False
            self.drawer.pack_forget()
        else:
            self._open_drawer()

    def _drain_logs(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                log(line)
                self.drawer.configure(state="normal")
                self.drawer.insert("end", line + "\n")
                self.drawer.see("end")
                self.drawer.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self._drain_logs)

    # ---- 生命周期 ----
    def on_close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.start_backend()
        self.root.mainloop()


def shell_log_path():
    """%APPDATA%\\tiangong-v3-qiyuan\\logs\\shell.log —— 与 bootstrap.py 默认 state 目录同根。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "tiangong-v3-qiyuan" / "logs" / "shell.log"


def fatal_dialog(message):
    # bug-fix: pre-UI 阶段静默失败改为弹窗 —— pythonw 无控制台，此前只写 stderr
    # 用户双击后"没反应"；同时落日志文件留证据（2026-08-26，凌霜修 UX）
    log(message)
    path = shell_log_path()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "天工造物 v3 启动失败", 0x10)
        except (AttributeError, OSError):
            pass


def main():
    enable_dpi_awareness()
    if ctk is None:
        # bug-fix: 缺 customtkinter 也走弹窗+日志，不再静默 return 1（2026-08-26，凌霜修 UX）
        fatal_dialog("缺少 customtkinter，无法启动桌面壳。请重新安装本应用以修复运行时依赖。")
        return 1
    shell_dir = Path(__file__).resolve().parent
    ini_path = shell_dir / INI_NAME
    app_root = Path(os.environ.get("TIANGONG_SHELL_APP_ROOT", str(shell_dir.parent / "app")))
    try:
        config = load_launcher_config(ini_path)
        port = find_available_port(config["port"])
        if port != config["port"]:
            log(f"端口 {config['port']} 被占，改用 {port}")
            config["port"] = port
            write_back_port(ini_path, port)
        command = build_child_command(app_root)
        env = build_child_env(config)
    except LauncherError as exc:
        # bug-fix: 缺 ini / 端口异常 / 运行时缺失弹窗告知具体原因（2026-08-26，凌霜修 UX）
        fatal_dialog(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 —— 未预期异常同样不能静默吞掉
        fatal_dialog(f"启动异常: {exc}")
        return 1
    # bug-fix: cwd 落 backend 目录兜底 ._pth 条目；目录不存在时退回默认 cwd（2026-08-26，凌霜修 UX）
    backend_dir = app_root / "backend"
    ShellApp(config, command, env, cwd=str(backend_dir) if backend_dir.is_dir() else None).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
