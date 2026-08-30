"""Authoritative source-ownership validation (single source of truth)."""

from .validator import (  # noqa: F401
    load_config,
    validate_source_authority,
)

__all__ = ["load_config", "validate_source_authority"]
