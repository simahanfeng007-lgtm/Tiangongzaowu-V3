"""Launch the existing 7184 gateway and optional mobile link with one Runtime.

The mobile listener is an adapter attached to the same GatewayRuntime instance.
It never starts a second AgentCore/Runtime and never changes the production
7184 loopback binding.
"""
from __future__ import annotations

import signal
import threading

from .bootstrap import GatewayConfig
from .mobile_link import create_mobile_link_server
from .mobile_omni_bridge import install_mobile_omni_bridge
from .runtime import GatewayRuntime
from .server import GatewayHttpServer


def run_gateway_with_mobile(config: GatewayConfig | None = None) -> None:
    resolved = GatewayConfig.from_environment() if config is None else config
    runtime = GatewayRuntime.start(resolved)
    gateway: GatewayHttpServer | None = None
    mobile = None
    mobile_thread: threading.Thread | None = None
    previous_handlers: dict[signal.Signals, object] = {}
    try:
        gateway = GatewayHttpServer(runtime)
        mobile = create_mobile_link_server(runtime)
        if mobile is not None:
            install_mobile_omni_bridge(runtime, mobile.broker)
            mobile_thread = threading.Thread(
                target=mobile.serve_forever,
                kwargs={"poll_interval": 0.25},
                name="tiangong-mobile-link",
                daemon=True,
            )
            mobile_thread.start()

        def request_shutdown(_signum: int, _frame: object) -> None:
            if gateway is not None:
                threading.Thread(
                    target=gateway.shutdown,
                    name="tiangong-gateway-drain",
                    daemon=True,
                ).start()
            if mobile is not None:
                threading.Thread(
                    target=mobile.shutdown,
                    name="tiangong-mobile-drain",
                    daemon=True,
                ).start()

        for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            shutdown_signal = getattr(signal, signal_name, None)
            if shutdown_signal is None or shutdown_signal in previous_handlers:
                continue
            try:
                previous_handlers[shutdown_signal] = signal.getsignal(shutdown_signal)
                signal.signal(shutdown_signal, request_shutdown)
            except (OSError, ValueError):
                previous_handlers.pop(shutdown_signal, None)
        gateway.serve_forever(poll_interval=0.25)
    finally:
        for shutdown_signal, previous in previous_handlers.items():
            try:
                signal.signal(shutdown_signal, previous)
            except (OSError, ValueError):
                pass
        if mobile is not None:
            try:
                mobile.shutdown()
            except Exception:
                pass
            mobile.server_close()
        if mobile_thread is not None and mobile_thread.is_alive():
            mobile_thread.join(timeout=2.0)
        if gateway is not None:
            gateway.server_close()
        runtime.close()


__all__ = ["run_gateway_with_mobile"]
