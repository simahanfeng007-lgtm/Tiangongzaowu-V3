"""Hard scope boundaries. Scope mismatch is never repaired by guessing."""
from __future__ import annotations
from collections.abc import Iterable
from contracts.world_understanding.scope import WorldScope
from .identity import same_scope_identity

class CommonScopeMismatch(ValueError):
    pass


def require_exact_scope(expected: WorldScope, actual: WorldScope) -> None:
    if not same_scope_identity(expected, actual):
        raise CommonScopeMismatch("SCOPE_MISMATCH")


def require_exact_scope_batch(expected: WorldScope, scopes: Iterable[WorldScope]) -> None:
    for scope in scopes:
        require_exact_scope(expected, scope)


def scope_contains(container: WorldScope, member: WorldScope) -> bool:
    """Containment is conservative and never crosses life/world/principal boundaries."""
    if container.life_id != member.life_id or container.world_id != member.world_id:
        return False
    if container.principal_scope_hash != member.principal_scope_hash:
        return False
    if container.domain_id != member.domain_id:
        return False
    outer = {(item.key, item.value) for item in container.scope_bindings}
    inner = {(item.key, item.value) for item in member.scope_bindings}
    return outer.issubset(inner)

__all__ = ["CommonScopeMismatch", "require_exact_scope", "require_exact_scope_batch", "scope_contains"]
