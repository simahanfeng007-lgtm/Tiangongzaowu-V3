"""Typed Gateway-to-Life callback wiring boundary.

This module owns only the dependency-installation contract between Total Gateway
and the existing EmbeddedLifeRuntime public setter surface.  It creates no
runtime, scheduler, store, writer, policy, or callback implementation.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Protocol, TypeAlias

GatewayCallback: TypeAlias = Callable[..., object]
OptionalGatewayCallback: TypeAlias = GatewayCallback | None


class EmbeddedLifeGatewayBinding(str, Enum):
    COGNITION_DECIDER = "cognition_decider"
    AUTONOMY_DECIDER = "autonomy_decider"
    LEARNING_DECIDER = "learning_decider"
    LEARNING_SHARE_WRITER = "learning_share_writer"
    PROACTIVE_DECIDER = "proactive_decider"
    PROACTIVE_EXPRESSION_WRITER = "proactive_expression_writer"
    PROACTIVE_WORLD_PROVIDER = "proactive_world_provider"
    SELF_ITERATION_DECIDER = "self_iteration_decider"
    UPGRADE_EXECUTOR = "upgrade_executor"
    GREETING_WRITER = "greeting_writer"
    ARTIFACT_ACTION_CATALOG_PROVIDER = "artifact_action_catalog_provider"
    ARTIFACT_PUBLISHER = "artifact_publisher"
    WORLD_IDENTITY_PROVIDER = "world_identity_provider"
    CAPABILITY_WORKSPACE_MAPPER = "capability_workspace_mapper"
    CAPABILITY_WORKSPACE_REMOVER = "capability_workspace_remover"
    CAPABILITY_WORKSPACE_MARKER = "capability_workspace_marker"
    CAPABILITY_PATCH_DECIDER = "capability_patch_decider"
    ARTIFACT_INVOKER = "artifact_invoker"


class EmbeddedLifeGatewayWiringPort(Protocol):
    def set_cognition_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_autonomy_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_learning_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_learning_share_writer(self, writer: OptionalGatewayCallback) -> None: ...
    def set_proactive_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_proactive_expression_writer(self, writer: OptionalGatewayCallback) -> None: ...
    def set_proactive_world_provider(self, provider: OptionalGatewayCallback) -> None: ...
    def set_self_iteration_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_upgrade_executor(self, executor: OptionalGatewayCallback) -> None: ...
    def set_greeting_writer(self, writer: OptionalGatewayCallback) -> None: ...
    def set_artifact_action_catalog_provider(self, provider: OptionalGatewayCallback) -> None: ...
    def set_artifact_publisher(self, publisher: OptionalGatewayCallback) -> None: ...
    def set_world_identity_provider(self, provider: OptionalGatewayCallback) -> None: ...
    def set_capability_workspace_mapper(self, mapper: OptionalGatewayCallback) -> None: ...
    def set_capability_workspace_remover(self, remover: OptionalGatewayCallback) -> None: ...
    def set_capability_workspace_marker(self, marker: OptionalGatewayCallback) -> None: ...
    def set_capability_patch_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_artifact_invoker(self, invoker: OptionalGatewayCallback) -> None: ...
    def set_learning_materializers(
        self,
        *,
        researcher: OptionalGatewayCallback = None,
        synthesizer: OptionalGatewayCallback = None,
    ) -> None: ...


def bind_embedded_life_gateway_callback(
    target: EmbeddedLifeGatewayWiringPort,
    binding: EmbeddedLifeGatewayBinding,
    callback: OptionalGatewayCallback,
) -> None:
    """Install one callback through the Runtime's existing public setter.

    Validation, locking, side effects, and exception semantics remain owned by
    the existing setter.  This function only removes setter-name knowledge from
    Total Gateway and keeps installation order at the caller.
    """
    if binding is EmbeddedLifeGatewayBinding.COGNITION_DECIDER:
        target.set_cognition_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.AUTONOMY_DECIDER:
        target.set_autonomy_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.LEARNING_DECIDER:
        target.set_learning_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.LEARNING_SHARE_WRITER:
        target.set_learning_share_writer(callback)
    elif binding is EmbeddedLifeGatewayBinding.PROACTIVE_DECIDER:
        target.set_proactive_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.PROACTIVE_EXPRESSION_WRITER:
        target.set_proactive_expression_writer(callback)
    elif binding is EmbeddedLifeGatewayBinding.PROACTIVE_WORLD_PROVIDER:
        target.set_proactive_world_provider(callback)
    elif binding is EmbeddedLifeGatewayBinding.SELF_ITERATION_DECIDER:
        target.set_self_iteration_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.UPGRADE_EXECUTOR:
        target.set_upgrade_executor(callback)
    elif binding is EmbeddedLifeGatewayBinding.GREETING_WRITER:
        target.set_greeting_writer(callback)
    elif binding is EmbeddedLifeGatewayBinding.ARTIFACT_ACTION_CATALOG_PROVIDER:
        target.set_artifact_action_catalog_provider(callback)
    elif binding is EmbeddedLifeGatewayBinding.ARTIFACT_PUBLISHER:
        target.set_artifact_publisher(callback)
    elif binding is EmbeddedLifeGatewayBinding.WORLD_IDENTITY_PROVIDER:
        target.set_world_identity_provider(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_WORKSPACE_MAPPER:
        target.set_capability_workspace_mapper(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_WORKSPACE_REMOVER:
        target.set_capability_workspace_remover(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_WORKSPACE_MARKER:
        target.set_capability_workspace_marker(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_PATCH_DECIDER:
        target.set_capability_patch_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.ARTIFACT_INVOKER:
        target.set_artifact_invoker(callback)
    else:
        raise ValueError(f"unsupported embedded life gateway binding: {binding!r}")


def bind_embedded_life_learning_materializers(
    target: EmbeddedLifeGatewayWiringPort,
    *,
    researcher: OptionalGatewayCallback = None,
    synthesizer: OptionalGatewayCallback = None,
) -> None:
    """Install the paired learning materializers without changing setter semantics."""
    target.set_learning_materializers(
        researcher=researcher,
        synthesizer=synthesizer,
    )


__all__ = [
    "EmbeddedLifeGatewayBinding",
    "EmbeddedLifeGatewayWiringPort",
    "GatewayCallback",
    "OptionalGatewayCallback",
    "bind_embedded_life_gateway_callback",
    "bind_embedded_life_learning_materializers",
]
