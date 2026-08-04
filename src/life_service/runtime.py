"""Source-ownership model for the future 7175 life authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


LIFE_SOURCE_SCHEMA = "tiangong.life.source-ownership.v1"
LIFE_SOURCE_VERSION = "1.0.0-p11-source-cutover"
LEGACY_API_CONTRACT = "tiangong.life.api.v2"
FUTURE_PORT = 7175
EXPECTED_BASELINE_SCHEMA = "tiangong.life.runtime-baseline.v1"


class LifeSourceMode(StrEnum):
    """Production writing exists only behind the signed P11 handoff."""

    DISABLED = "disabled"
    STATUS_ONLY = "status_only"
    SHADOW_READ_ONLY = "shadow_read_only"
    PRODUCTION_SINGLE_WRITER = "production_single_writer"


@dataclass(frozen=True, slots=True)
class LifeSourceOwnershipReport:
    schema: str
    version: str
    mode: LifeSourceMode
    legacy_api_contract: str
    future_port: int
    production_writer_enabled: bool
    network_listener_enabled: bool
    scheduler_enabled: bool
    real_data_mutation_enabled: bool
    baseline_present: bool
    baseline_sha256: str | None
    original_life_core_source_available: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_present": self.baseline_present,
            "baseline_sha256": self.baseline_sha256,
            "future_port": self.future_port,
            "legacy_api_contract": self.legacy_api_contract,
            "mode": self.mode.value,
            "network_listener_enabled": self.network_listener_enabled,
            "original_life_core_source_available": self.original_life_core_source_available,
            "production_writer_enabled": self.production_writer_enabled,
            "real_data_mutation_enabled": self.real_data_mutation_enabled,
            "scheduler_enabled": self.scheduler_enabled,
            "schema": self.schema,
            "version": self.version,
        }


def _discover_workspace_root(explicit: Path | None) -> Path | None:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        return candidate if candidate.is_dir() else None
    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "manifest.json").is_file():
            return parent
    return None


def _load_baseline(root: Path | None) -> tuple[bool, str | None, bool | None]:
    if root is None:
        return False, None, None
    path = root / "baselines" / "life-runtime-p0.json"
    if not path.is_file() or path.is_symlink():
        return False, None, None
    data = path.read_bytes()
    payload = json.loads(data.decode("utf-8", errors="strict"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != EXPECTED_BASELINE_SCHEMA
        or not isinstance(payload.get("original_life_core_source_available"), bool)
    ):
        raise ValueError("life runtime baseline is invalid")
    return (
        True,
        hashlib.sha256(data).hexdigest(),
        bool(payload["original_life_core_source_available"]),
    )


def build_source_ownership_report(
    workspace_root: Path | None = None,
) -> LifeSourceOwnershipReport:
    root = _discover_workspace_root(workspace_root)
    baseline_present, baseline_sha256, original_source = _load_baseline(root)
    return LifeSourceOwnershipReport(
        schema=LIFE_SOURCE_SCHEMA,
        version=LIFE_SOURCE_VERSION,
        mode=LifeSourceMode.STATUS_ONLY,
        legacy_api_contract=LEGACY_API_CONTRACT,
        future_port=FUTURE_PORT,
        production_writer_enabled=False,
        network_listener_enabled=False,
        scheduler_enabled=False,
        real_data_mutation_enabled=False,
        baseline_present=baseline_present,
        baseline_sha256=baseline_sha256,
        original_life_core_source_available=original_source,
    )


__all__ = [
    "EXPECTED_BASELINE_SCHEMA",
    "FUTURE_PORT",
    "LEGACY_API_CONTRACT",
    "LIFE_SOURCE_SCHEMA",
    "LIFE_SOURCE_VERSION",
    "LifeSourceMode",
    "LifeSourceOwnershipReport",
    "build_source_ownership_report",
]
