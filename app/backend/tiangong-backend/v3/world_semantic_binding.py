"""Automatically bind L4 to the model channel already owned by V3.

No new credentials, HTTP client, tool route, or provider selection authority.
Configuration is resolved for each attempted call; construction is offline.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import logging
import os
import threading
import time
import weakref

from world_understanding.semantic.model import SemanticModelDeferred, SemanticModelUnavailable
from world_understanding.semantic.v3_http_adapter import V3HttpSemanticModel

from .jineng.model_call_lifecycle import ModelCallStopped, run_model_call
from .model_endpoint import duqu_model_endpoint_config
from .peizhi import duqu_endpoint_api_miyao
from .run_context import current_run_context

_log = logging.getLogger("tiangong.world_semantic")
_client_ref = None


def bind_world_semantic_client(client) -> None:
    """Called by the existing dispatcher composition, including its offline mode."""
    global _client_ref
    _client_ref = None if client is None else weakref.ref(client)


def _client():
    return None if _client_ref is None else _client_ref()


@dataclass(frozen=True, slots=True)
class WorldSemanticBudget:
    timeout_seconds: float = 15.0
    min_interval_seconds: float = 30.0
    max_calls_per_run: int = 1
    max_output_tokens: int = 2048
    max_input_chars: int = 64_000

    def __post_init__(self):
        if (not 0 < self.timeout_seconds <= 60 or self.min_interval_seconds < 0
                or not 1 <= self.max_calls_per_run <= 4
                or not 128 <= self.max_output_tokens <= 4096
                or not 1 <= self.max_input_chars <= 256_000):
            raise ValueError("WORLD_SEMANTIC_BUDGET_INVALID")


class ConfiguredWorldSemanticModel:
    """Lazy binding with bounded attempts; it never falls back to another model."""

    def __init__(self, *, client_provider=None, endpoint_resolver=None,
                 credential_reader=None, budget=None, clock=None):
        self.client_provider = client_provider or _client
        self.endpoint_resolver = endpoint_resolver or duqu_model_endpoint_config
        self.credential_reader = credential_reader or duqu_endpoint_api_miyao
        self.budget = budget or WorldSemanticBudget()
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._attempts = OrderedDict()
        self._last_calls = OrderedDict()
        self._inflight = None
        self.last_status = "UNBOUND"

    def _binding(self):
        enabled = os.environ.get("TIANGONG_WORLD_SEMANTIC_ENABLED", "1").strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            self.last_status = "DISABLED"
            return None
        client = self.client_provider()
        if client is None or not callable(getattr(client, "scoped_semantic_inference", None)):
            self.last_status = "UNBOUND"
            return None
        if getattr(getattr(client, "_kehuduan", None), "is_closed", False):
            self.last_status = "UNBOUND"
            return None
        try:
            endpoint = self.endpoint_resolver()
            if not endpoint.model_name or not endpoint.base_url or not self.credential_reader(endpoint.provider_identity, endpoint.base_url):
                self.last_status = "UNCONFIGURED"
                return None
        except Exception:
            self.last_status = "UNCONFIGURED"
            return None
        return client, endpoint

    def is_available(self) -> bool:
        return self._binding() is not None

    @staticmethod
    def _remember(table, key, value):
        table[key] = value
        table.move_to_end(key)
        while len(table) > 512:
            table.popitem(last=False)

    def _defer(self, reason):
        self.last_status = reason
        raise SemanticModelDeferred(reason)

    def generate(self, request):
        binding = self._binding()
        if binding is None:
            raise SemanticModelUnavailable("configured V3 model channel unavailable")
        if len(request.payload_json) > self.budget.max_input_chars:
            self._defer("SEMANTIC_INPUT_BUDGET")
        from .jineng.http_kehuduan import _effective_llm_deadline_seconds
        call_seconds = min(self.budget.timeout_seconds, _effective_llm_deadline_seconds())
        if call_seconds <= 0:
            self._defer("SEMANTIC_DEADLINE_BUDGET")
        client, endpoint = binding
        scope = json.loads(request.payload_json).get("scope", {})
        context = current_run_context()
        scope_key = (scope.get("life_id"), scope.get("world_scope_hash"), scope.get("principal_scope_hash"))
        run_id = context.run_id or context.request_id
        run_key = (*scope_key, run_id, context.generation)
        now = self.clock()
        attempt = object()
        started = threading.Event()
        with self._lock:
            if self._inflight:
                self._defer("SEMANTIC_BUSY")
            if run_id and self._attempts.get(run_key, 0) >= self.budget.max_calls_per_run:
                self._defer("SEMANTIC_RUN_BUDGET")
            if now - self._last_calls.get(scope_key, float("-inf")) < self.budget.min_interval_seconds:
                self._defer("SEMANTIC_COOLDOWN")
            self._inflight = attempt
            self._remember(self._last_calls, scope_key, now)
            if run_id:
                self._remember(self._attempts, run_key, self._attempts.get(run_key, 0) + 1)

        def infer(lifecycle):
            started.set()
            try:
                with client.scoped_semantic_inference(endpoint=endpoint, max_output_tokens=self.budget.max_output_tokens):
                    lifecycle.check()
                    return V3HttpSemanticModel(client, endpoint.provider_identity, endpoint.model_name).generate(request)
            finally:
                # If DNS/network code ignores cancellation, don't start another
                # auxiliary worker until that original call really exits.
                with self._lock:
                    if self._inflight is attempt:
                        self._inflight = None

        self.last_status = "RUNNING"
        try:
            response = run_model_call(infer, seconds=call_seconds, child=True)
        except ModelCallStopped as exc:
            if not started.is_set():
                with self._lock:
                    if self._inflight is attempt:
                        self._inflight = None
            self.last_status = "TIMEOUT" if exc.reason == "deadline_exceeded" else "CANCELLED"
            _log.warning("world_semantic status=%s", self.last_status)
            raise SemanticModelUnavailable("world semantic call stopped") from exc
        except Exception as exc:
            self.last_status = "UNAVAILABLE"
            _log.warning("world_semantic status=UNAVAILABLE")
            raise SemanticModelUnavailable("configured V3 semantic call unavailable") from exc
        self.last_status = "RESPONDED"
        return response


__all__ = ["ConfiguredWorldSemanticModel", "WorldSemanticBudget", "bind_world_semantic_client"]
