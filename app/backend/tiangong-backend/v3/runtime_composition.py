"""Typed composition root for the existing V3 ``Zongdiaodu`` runtime.

This module constructs only dependencies already used by the one production
``Zongdiaodu``. It does not create a second entrypoint, scheduler, state
authority, or execution path.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .guancha_pinggu.guancha import GuanchaYinqing
from .gutong.gutong_ceng import GutongCeng
from .jineng.http_kehuduan import HttpKehuduan
from .jinhua.biaoda_router import JinhuaBiaodaRouter
from .jinhua.bihuan_yinqing import JinhuaBihuanYinqing
from .jinhua.yinqing import JinhuaYinqing
from .ziyu.yinqing import ZiyuYinqing


class LockPort(Protocol):
    """Minimal lock contract consumed by the orchestration host."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...
    def release(self) -> None: ...
    def __enter__(self) -> "LockPort": ...
    def __exit__(
        self,
        exc_type: object | None,
        exc_value: object | None,
        traceback: object | None,
    ) -> bool | None: ...


@dataclass(slots=True)
class ZongdiaoduComposition:
    """Concrete dependencies assembled once for one dispatcher instance."""

    http_kehuduan: HttpKehuduan | None
    gutong: GutongCeng
    guancha_yq: GuanchaYinqing
    jinhua_yq: JinhuaYinqing
    jinhua_biaoda: JinhuaBiaodaRouter
    jinhua_bihuan: JinhuaBihuanYinqing
    ziyu_yq: ZiyuYinqing
    lifecycle_lock: LockPort
    active_user_run_lock: LockPort


def build_zongdiaodu_composition(
    llm_diaoyong_han_shu: Callable[..., object] | None = None,
) -> ZongdiaoduComposition:
    """Build the dependency set historically constructed in ``Zongdiaodu.__init__``."""

    if llm_diaoyong_han_shu is not None:
        http_kehuduan = None
        gutong = GutongCeng(llm_diaoyong_han_shu)
    else:
        http_kehuduan = HttpKehuduan()
        gutong = GutongCeng(http_kehuduan.zuowei_huidiao())

    return ZongdiaoduComposition(
        http_kehuduan=http_kehuduan,
        gutong=gutong,
        guancha_yq=GuanchaYinqing(),
        jinhua_yq=JinhuaYinqing(),
        jinhua_biaoda=JinhuaBiaodaRouter(),
        jinhua_bihuan=JinhuaBihuanYinqing(),
        ziyu_yq=ZiyuYinqing(),
        lifecycle_lock=threading.Lock(),
        active_user_run_lock=threading.Lock(),
    )


__all__ = [
    "LockPort",
    "ZongdiaoduComposition",
    "build_zongdiaodu_composition",
]
