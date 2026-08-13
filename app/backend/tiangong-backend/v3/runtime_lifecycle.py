"""Lifecycle port for the existing V3 ``Zongdiaodu`` host.

Startup ordering is intentionally identical to the former inline methods.
The host remains the one production ``Zongdiaodu`` instance registered with
the existing bridge; no alternate runtime or scheduler is created here.
"""
from __future__ import annotations

from typing import Protocol

from .duihua_qiaojie import QIAOJIE
from .shenti_zhuangtai import ShentiZhuangtai
from .zhuangtai_tongbu import TONGBU


class HeartbeatPort(Protocol):
    yunxing_zhong: bool

    def gengxin_shenti(self, shenti: ShentiZhuangtai) -> None: ...
    def qidong(self) -> None: ...
    def tingzhi(self) -> None: ...
    def _yici_tick(self, shenti: ShentiZhuangtai) -> None: ...


class ZongdiaoduLifecycleHost(Protocol):
    xintiao: HeartbeatPort

    @property
    def shenti(self) -> ShentiZhuangtai: ...

    def _cleanup_stale_run_states(self) -> None: ...


class DetachedLegacyHeartbeat:
    """Compatibility surface after the legacy life chain was detached."""

    yunxing_zhong = False

    def gengxin_shenti(self, _shenti: ShentiZhuangtai) -> None:
        return None

    def qidong(self) -> None:
        return None

    def tingzhi(self) -> None:
        return None

    def _yici_tick(self, _shenti: ShentiZhuangtai) -> None:
        return None


def start_zongdiaodu_runtime(
    host: ZongdiaoduLifecycleHost,
    *,
    life_chain_enabled: bool,
) -> None:
    """Start the existing runtime in the historical production order."""

    host._cleanup_stale_run_states()
    if life_chain_enabled:
        host.xintiao.gengxin_shenti(host.shenti)
        host.xintiao.qidong()
    TONGBU.qidong()
    QIAOJIE.shezhi_zongdiaodu(host)
    QIAOJIE.qidong()


def stop_zongdiaodu_runtime(
    host: ZongdiaoduLifecycleHost,
    *,
    life_chain_enabled: bool,
) -> None:
    """Preserve the historical stop behavior exactly."""

    if life_chain_enabled:
        host.xintiao.tingzhi()


__all__ = [
    "DetachedLegacyHeartbeat",
    "HeartbeatPort",
    "ZongdiaoduLifecycleHost",
    "start_zongdiaodu_runtime",
    "stop_zongdiaodu_runtime",
]
