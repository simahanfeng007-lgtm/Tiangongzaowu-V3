"""Persistent production assembly from ACTIVE inbound request to channel receipt."""

from __future__ import annotations

from .diagnostics import diagnostic_log

import base64
import concurrent.futures
import contextvars
import hashlib
from contextlib import contextmanager
import os
import queue
import threading
import time
import uuid
import traceback
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from dataclasses import asdict

from contracts import (
    ActionIntent,
    ActionPermission,
    ActionRegistrySnapshot,
    CapabilityAction,
    CapabilityManifest,
    ExecutionTicketPayload,
    ResourceEnvelope,
    ObjectGrant,
    OutboundPart,
    OutboundPlan,
    OutboundScope,
    TransitionEvent,
    canonical_json_bytes,
    canonical_sha256,
    derive_delivery_identity,
    derive_effect_identity,
    derive_outbound_scope_keys,
    new_state_snapshot,
    text_sha256,
)

from .active_requests import ActiveRequestActivator
from .action_registry import load_action_registry
from .impact_evaluator import compute_action_impact, derive_impact_knobs, probe_target_state
from .omni_grant_authority import OmniGrantAuthority
from .policy_engine import PolicyEngine, SourceRef
from .policy_evidence import PolicyEvidenceLedger
from .artifact_gate import ArtifactCandidate, ArtifactGate, ArtifactGateError
from .artifact_qc import ArtifactIntegrityQcError, ArtifactIntegrityQcService
from .backend_client import BackendClient, BackendClientError


_EXECUTION_WATCHDOG_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="execution-watchdog",
)


def _append_orchestration_effect_event(
    store,
    *,
    event_key: str,
    event_type: str,
    payload: dict[str, object],
    request_id: str,
    run_id: str,
    generation: int,
    effect_id: str,
    created_at_ms: int,
) -> bool:
    """Append the unified execution event when this Run has provider authority.

    GatewayOrchestrationWorker and RegenerativeExecutionAuthority share the
    canonical effect ledger.  Historically only the provider appended step.*
    events to the execution ledger, which made crash recovery misread
    orchestration-owned effects as ledger corruption.  This helper closes that
    gap: whenever the regenerative provider has initialized the task contract
    and generation authority for the Run, the orchestration path records the
    same step.prepared / step.dispatched / terminal events.

    Orchestration-only flows (life capability or delivery requests that never
    initialize the provider) keep the canonical effect ledger as their sole
    authority and are skipped here; crash recovery already finalizes such
    effects conservatively (AMBIGUOUS / skip).
    """
    try:
        contract = store.get_execution_task_contract(
            request_id, run_id=run_id, generation=generation
        )
        binding = store.get_request_generation_binding(request_id)
    except Exception:
        return False
    if contract is None or binding is None:
        return False
    if (
        str(binding.get("run_id") or "") != run_id
        or int(binding.get("current_generation") or -1) != generation
        or str(binding.get("status") or "") != "ACTIVE"
    ):
        return False
    # Execution ledger identities must satisfy the canonical id patterns
    # (lef_/att_/stp_ + 64 hex).  Derive them deterministically from the
    # canonical effect id so every append is idempotent across retries.
    hex_suffix = (
        effect_id[4:]
        if effect_id.startswith("eff_") and len(effect_id) == 68
        else hashlib.sha256(effect_id.encode("utf-8")).hexdigest()
    )
    try:
        store.append_execution_event(
            event_key=event_key,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            epoch_index=0,
            event_type=event_type,
            created_at_ms=created_at_ms,
            payload=payload,
            logical_effect_id=f"lef_{hex_suffix}",
            attempt_id=f"att_{hex_suffix}",
            step_id=f"stp_{hex_suffix}",
            effect_id=effect_id,
        )
        return True
    except Exception:
        return False
from .communication_client import CommunicationControlClient
from .context_projection import SessionContextProjector, estimate_projected_context_tokens
from .continuity import (
    persist_compression_checkpoint,
    persist_interruption_checkpoint,
    persist_terminal_completion,
    persist_working_checkpoint,
)
from .delivery_outbox import GatewayDeliveryOutboxWorker, build_delivery_outbox_payload
from .desktop_completion import evaluate_desktop_completion
from .docx_qc import DocxQcError, DocxQcPolicy, DocxQcService
from .effects import EffectClaim, EffectResult
from .fact_ledger import FactLedger
from .frozen_backend_compat import FrozenBackendCompatibilityTransport
from .gateway_url import DEFAULT_GATEWAY_URL, normalize_gateway_url
from .life_client import LifeClient, LifeJsonTransport, LifeProfileBindings, LoopbackLifeJsonTransport
from .object_store import ContentAddressedObjectStore
from .outbox import OutboxIntent, derive_outbox_id
from .release_manifest import (
    generate_release_manifest,
    release_manifest_bytes,
    select_latest_release_manifest_with_path,
)
from .runtime_authority import RuntimeTicketAuthority
from .skill_selection import (
    SkillSelectionService,
    load_filesystem_skill_catalog,
    load_model_capability_manifest,
)
from .skill_authority import SkillAuthority
from .store import (
    ActiveRequestActivation,
    GatewayStateStore,
    StoreCasConflict,
    StoreConflictError,
    StoreError,
)
from contracts.world_understanding.inquiry import WorldInquiry
from world_understanding.inquiry.self_will_integration import ExistingSelfWillAdapter

def _verification_snapshot(store, snapshot_sha256: str):
    """Load the authoritative RegistrySnapshot by hash from the store."""
    snapshot = store.get_verification_registry_snapshot_by_sha256(
        snapshot_sha256
    )
    if snapshot is None:
        raise OrchestrationError(
            "verification registry snapshot missing from store"
        )
    return snapshot


_WECHAT_POLICY_SHA256 = "d486cbb41e0e95a7b8ac9ea5aed6ef1efe9c74ff13e67cb2d17cd8af93116df7"
_FEISHU_POLICY_SHA256 = "180585fe5d5e5967a472628ff72ea7d92bc96cb8a6a8949f872f9423f73fa05f"


def manifest_authority_scope(component_manifest_sha256: str) -> str:
    """Return a bounded physical namespace for one component manifest.

    The authority record still stores and verifies the complete 256-bit
    manifest digest.  The directory is only a lookup namespace, so a 160-bit
    prefix gives a far larger collision margin than the number of releases we
    can ever retain while keeping the deepest DPAPI atomic-write path below
    Windows' legacy MAX_PATH boundary for redirected/deep user profiles.  A
    hypothetical prefix collision fails closed when the full digest in
    ``authority.json`` is checked.
    """

    try:
        raw = bytes.fromhex(component_manifest_sha256)
    except ValueError as exc:
        raise ValueError("component manifest digest is invalid") from exc
    if len(raw) != 32 or component_manifest_sha256 != component_manifest_sha256.lower():
        raise ValueError("component manifest digest is invalid")
    return base64.urlsafe_b64encode(raw[:20]).decode("ascii").rstrip("=")


class OrchestrationError(RuntimeError):
    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


def _model_safe_knowledge_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project retrieval evidence without host paths or raw storage metadata."""
    projected: dict[str, Any] = {}
    for key in (
        "document_id", "title", "file_name", "summary", "score",
        "citation_count", "extraction_status", "content_extract",
    ):
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) and not isinstance(item, bytes):
            projected[key] = item
    for key in ("key_points", "keywords"):
        items = value.get(key)
        if isinstance(items, list):
            projected[key] = [
                str(item)[:500]
                for item in items[:16]
                if isinstance(item, (str, int, float))
            ]
    raw_matches = value.get("matches")
    if isinstance(raw_matches, list):
        projected["matches"] = []
        for raw in raw_matches[:4]:
            if not isinstance(raw, Mapping):
                continue
            match = {
                key: raw.get(key)
                for key in ("local_id", "citation_id", "title", "text", "score", "kind")
                if isinstance(raw.get(key), (str, int, float, bool))
            }
            if "text" in match:
                match["text"] = str(match["text"])[:1800]
            projected["matches"].append(match)
    projected["safe_projection_only"] = True
    return projected


def compatibility_capability_manifest(component_manifest_hash: str, *, generated_at_ms: int) -> CapabilityManifest:
    argument_schema = canonical_sha256(
        {
            "schema": "tiangong.gateway.frozen-7174-arguments.v1",
            "required": ["attachments", "life_snapshot", "text"],
            "host_paths_permitted": False,
        }
    )
    result_schema = canonical_sha256(
        {
            "schema": "tiangong.gateway.frozen-7174-result.v1",
            "reply_text_required": True,
            "artifact_paths_exported_as_object_refs": True,
        }
    )
    action = CapabilityAction(
        action_id="gateway.model.run",
        version="1.0.0",
        provider_component_id="tiangong-backend",
        argument_schema_sha256=argument_schema,
        result_schema_sha256=result_schema,
        # This outer ticket authorizes one bounded model run, not its inner
        # tools and not channel delivery.  Inner A4 tools require a separate
        # signed OmniCapabilityGrant; delivery has its own DeliveryTicket.
        risk_class="A3",
        allowed_side_effects=("local_write", "read"),
        idempotency_mode="non_retriable",
        # Long-form deliverables legitimately cross the old 15 minute wall.
        # The renderer already uses activity-based liveness, while the frozen
        # backend persists checkpoints and can resume after context compaction.
        # Keep a bounded one-hour execution lease so the gateway does not turn
        # a healthy long task into an ambiguous, detached background run.
        max_runtime_ms=3_600_000,
        max_output_bytes=536_870_912,
        max_tool_calls=1_000,
        available=True,
        model_visible=False,
    )
    return CapabilityManifest(
        manifest_id="frozen-7174-compatibility-v1",
        revision=1,
        generated_at_ms=generated_at_ms,
        component_manifest_hash=component_manifest_hash,
        actions=(action,),
        sha256="0" * 64,
    ).with_computed_sha256()


def compatibility_action_registry(
    manifest: CapabilityManifest,
    *,
    generated_at_ms: int,
) -> ActionRegistrySnapshot:
    action = manifest.actions[0]
    permission = ActionPermission(
        action_id=action.action_id,
        action_version=action.version,
        registry_risk=action.risk_class,
        effective_risk=action.risk_class,
        effect="execute",
        handler="FrozenBackendCompatibilityTransport.execute",
        allowed_side_effects=action.allowed_side_effects,
        path_policy="no_path",
        allow_absolute_paths=False,
        allow_shell=False,
        allow_python=False,
        requires_confirmation=False,
        source_manifest_sha256=manifest.sha256,
        permission_sha256="0" * 64,
    ).with_computed_sha256()
    return ActionRegistrySnapshot(
        registry_id="gateway-model-run-registry",
        revision=1,
        generated_at_ms=generated_at_ms,
        source_manifest_sha256=manifest.sha256,
        executable_count=1,
        permissions=(permission,),
        registry_sha256="0" * 64,
    ).with_computed_sha256()


class GatewayOrchestrationWorker:
    def __init__(
        self,
        *,
        activator: ActiveRequestActivator,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        facts: FactLedger,
        authority: RuntimeTicketAuthority,
        release_manifest,
        release_manifest_path: Path | None,
        component_manifest,
        workspace_root: Path,
        backend_token: str,
        life_token: str,
        communication_token: str,
        gateway_epoch: int,
        gateway_instance_id: str,
        life_transport: LifeJsonTransport | None = None,
        communication_control: object | None = None,
        backend_compat_client: object | None = None,
        life_compat_client: object | None = None,
        life_execution_commit: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        repository_evidence_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
        knowledge_retriever: Callable[[str], Mapping[str, Any]] | None = None,
        skill_selection: SkillSelectionService | None = None,
        skill_capabilities: CapabilityManifest | None = None,
        omni_registry: ActionRegistrySnapshot | None = None,
        policy_evidence: PolicyEvidenceLedger | None = None,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if not workspace_root.is_absolute() or not workspace_root.is_dir() or workspace_root.is_symlink():
            raise ValueError("orchestration workspace is missing or unsafe")
        self._activator = activator
        self._store = store
        self._objects = objects
        self._facts = facts
        self._authority = authority
        self._release_manifest = release_manifest
        self._release_manifest_path = release_manifest_path
        self._components = component_manifest
        self._workspace_root = workspace_root.resolve(strict=True)
        self._backend_token = backend_token
        self._life_token = life_token
        self._life_transport = life_transport
        self._backend_compat_client = backend_compat_client
        self._life_compat_client = life_compat_client
        self._life_execution_commit = life_execution_commit
        self._repository_evidence_provider = repository_evidence_provider
        self._knowledge_retriever = knowledge_retriever
        self._communication = (
            communication_control
            if communication_control is not None
            else CommunicationControlClient(communication_token)
        )
        self._epoch = gateway_epoch
        self._instance_id = gateway_instance_id
        self._gateway_url = normalize_gateway_url(gateway_url)
        self._skills = skill_selection
        self._skill_capabilities = skill_capabilities
        self._skill_authority = (
            None
            if skill_selection is None or skill_capabilities is None
            else SkillAuthority(skill_selection, skill_capabilities, store, facts)
        )

        if omni_registry is None or policy_evidence is None or skill_capabilities is None:
            raise ValueError("production orchestration requires Omni registry and policy evidence")
        self._policy_evidence = policy_evidence
        self._omni_grants = OmniGrantAuthority(
            registry=omni_registry,
            capability_manifest_hash=self._release_manifest.capability_manifest_sha256,
            component_manifest_hash=self._components.manifest_sha256,
            skill_catalog_hash=self._release_manifest.skill_catalog_sha256,
            signer=self._authority.execution_signer,
            gateway_epoch=self._epoch,
            workspace_root=self._workspace_root,
            evidence=self._policy_evidence,
            trust_bundle_provider=lambda now_ms: self._authority.execution_trust_bundle(
                gateway_epoch=self._epoch,
                now_ms=now_ms,
            ),
            effect_store=store,
            gateway_url=self._gateway_url,
        )
        self._context_projector = SessionContextProjector(store, objects)
        self._poll_seconds = poll_interval_seconds
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error_lock = threading.Lock()
        self._last_error: str | None = None
        self._processed_count = 0
        # P13.2 reuses this worker thread.  The bounded queue is only an input
        # lane; it is neither a scheduler nor a second execution loop.
        self._world_inquiries: queue.Queue[tuple[WorldInquiry, Callable[[Mapping[str, object]], None]]] = queue.Queue(maxsize=64)
        self._delivery_outbox = GatewayDeliveryOutboxWorker(
            store=store,
            objects=objects,
            facts=facts,
            authority=authority,
            component_manifest=component_manifest,
            communication=self._communication,
            gateway_epoch=gateway_epoch,
            worker_id=gateway_instance_id,
            advance=self._advance,
        )

    def _life_transport_for_execution(self) -> LifeJsonTransport:
        """Return the single Life authority selected for this deployment.

        Embedded execution has an in-process Life transport.  Falling back to
        a retired loopback listener when that dependency was not wired would
        silently split one request across two authorities, so fail closed.
        The loopback branch remains only for explicit non-embedded test and
        migration deployments that do not provide an embedded Life client.
        """
        transport = getattr(self, "_life_transport", None)
        if transport is not None:
            return transport
        if getattr(self, "_life_compat_client", None) is not None:
            raise OrchestrationError("orchestration.life.inprocess_transport_missing")
        return LoopbackLifeJsonTransport(
            "http://127.0.0.1:7175",
            desktop_token=self._life_token,
        )

    @classmethod
    def from_runtime_config(
        cls,
        *,
        config,
        activator: ActiveRequestActivator,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        facts: FactLedger,
        gateway_epoch: int,
        gateway_instance_id: str,
        now_ms: int,
        life_transport: LifeJsonTransport | None = None,
        communication_control: object | None = None,
        backend_compat_client: object | None = None,
        life_compat_client: object | None = None,
        life_execution_commit: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        repository_evidence_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
        knowledge_retriever: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> "GatewayOrchestrationWorker":
        release_candidates = tuple(
            path
            for path in (
                config.release_manifest_path,
                *tuple(getattr(config, "release_manifest_candidates", ()) or ()),
            )
            if path is not None
        )
        if release_candidates:
            release_path, release = select_latest_release_manifest_with_path(
                release_candidates,
                require_production=config.environment == "production",
            )
            if config.environment != "production" and config.release_source_root is not None:
                current_source_release = generate_release_manifest(config.release_source_root)
                if release_manifest_bytes(release) != release_manifest_bytes(current_source_release):
                    # Development source is the authority.  Never execute changed
                    # code under a stale manifest merely because the stale file
                    # still has a valid self-digest.  Use an in-memory source
                    # authority until the source manifest is regenerated.
                    diagnostic_log("[ORCH-RELEASE] stale development manifest ignored; using current source authority")
                    release = current_source_release
                    release_path = None
        elif config.environment != "production" and config.release_source_root is not None:
            release = generate_release_manifest(config.release_source_root)
            release_path = None
        else:
            raise OrchestrationError("orchestration.release_manifest.missing")
        # Ticket keys are cryptographically bound to one component manifest.
        # A legitimate product upgrade therefore needs a new keyset; reopening
        # the legacy unscoped directory makes every changed release fail with
        # ``runtime authority is bound to another component manifest``.  Keep
        # each keyset in a manifest-scoped directory in every environment.
        # Existing unscoped production authority files remain untouched for
        # audit/rollback, while repeated starts of the same release reuse the
        # same scoped authority.
        # The full 256-bit manifest digest remains the record authority.  The
        # bounded physical namespace prevents the deeper DPAPI temporary paths
        # from crossing MAX_PATH on redirected/deep Windows profiles.
        authority_root = (
            config.state_root
            / "ta"
            / "m"
            / manifest_authority_scope(release.component_manifest.manifest_sha256)
        )
        runtime_key_protector = getattr(config, "runtime_key_protector", None)
        if runtime_key_protector is None and os.name != "nt" and config.environment in {"development", "test"}:
            from runtime_security import ephemeral_test_protector_for_scope

            runtime_key_protector = ephemeral_test_protector_for_scope(
                f"runtime-ticket-authority:{authority_root}"
            )
        authority = RuntimeTicketAuthority.open(
            authority_root,
            release.component_manifest,
            now_ms=now_ms,
            protector=runtime_key_protector,
        )
        skill_selection = None
        skill_capabilities = None
        omni_registry = None
        skill_root = getattr(config, "skill_root", None)
        if skill_root is None and config.release_source_root is not None:
            skill_root = (
                config.release_source_root
                / "app"
                / "backend"
                / "tiangong-backend"
                / "_internal"
                / "omni_body_skill"
            )
        if skill_root is None:
            raise OrchestrationError("orchestration.skill_catalog.missing")
        else:
            loaded = load_filesystem_skill_catalog(
                skill_root,
                expected_index_sha256=release.skill_index_sha256,
                expected_catalog_sha256=release.skill_catalog_sha256,
            )
            skill_selection = SkillSelectionService(loaded.catalog)
            capability_path = skill_root / "registry" / "capability_manifest.generated.json"
            skill_capabilities = load_model_capability_manifest(
                capability_path,
                expected_sha256=release.capability_manifest_sha256,
                component_manifest_hash=release.component_manifest.manifest_sha256,
                generated_at_ms=release.generated_at_ms,
            ).manifest
            omni_registry = load_action_registry(
                capability_path.resolve(strict=True),
                generated_at_ms=release.generated_at_ms,
            )
        assert config.workspace_root is not None
        return cls(
            activator=activator,
            store=store,
            objects=objects,
            facts=facts,
            authority=authority,
            release_manifest=release,
            release_manifest_path=release_path,
            component_manifest=release.component_manifest,
            workspace_root=config.workspace_root,
            backend_token=config.backend_internal_token,
            life_token=config.life_internal_token,
            communication_token=config.communication_api_token,
            gateway_epoch=gateway_epoch,
            gateway_instance_id=gateway_instance_id,
            life_transport=life_transport,
            communication_control=communication_control,
            backend_compat_client=backend_compat_client,
            life_compat_client=life_compat_client,
            life_execution_commit=life_execution_commit,
            repository_evidence_provider=repository_evidence_provider,
            knowledge_retriever=knowledge_retriever,
            skill_selection=skill_selection,
            skill_capabilities=skill_capabilities,
            omni_registry=omni_registry,
            # Keep the evidence root compact: content addresses are 64 hex
            # characters and must remain writable under deep Windows profiles.
            policy_evidence=PolicyEvidenceLedger(config.state_root / "p"),
            gateway_url=getattr(config, "gateway_url", DEFAULT_GATEWAY_URL),
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        # V14 草案 §3.2：启动时先把上次崩溃遗留的 STARTED attempt 收口为
        # RECONCILE_REQUIRED（head 层 AMBIGUOUS），进入只读对账；
        # 未完成提交不再悬挂（此前 recover_started_effects 无生产调用方）。
        try:
            recovered = self._store.recover_started_effects(
                now_ms=time.time_ns() // 1_000_000
            )
            if recovered:
                diagnostic_log(
                    f"[ORCHESTRATION] recovered {len(recovered)} started effects into reconcile-required"
                )
        except Exception as exc:  # noqa: BLE001 - 恢复失败不得阻止 worker 启动，但必须留痕
            diagnostic_log(f"[ORCHESTRATION] started-effect recovery failed: {type(exc).__name__}: {exc}")
        self._thread = threading.Thread(
            target=self._run,
            name="tiangong-gateway-orchestration",
            daemon=True,
        )
        self._thread.start()

    def submit_world_inquiry(
        self,
        inquiry: WorldInquiry,
        result_sink: Callable[[Mapping[str, object]], None],
    ) -> bool:
        """Offer one zero-authority inquiry to the existing worker."""
        if (
            not isinstance(inquiry, WorldInquiry)
            or not inquiry.has_valid_hash()
            or inquiry.authorization != "NONE"
            or inquiry.may_execute
            or inquiry.may_call_tools
            or not callable(result_sink)
        ):
            raise ValueError("WORLD_INQUIRY_GATEWAY_INPUT_INVALID")
        try:
            self._world_inquiries.put_nowait((inquiry, result_sink))
        except queue.Full:
            return False
        return True

    @property
    def component_manifest(self):
        return self._components

    @property
    def release_manifest(self):
        return self._release_manifest

    @property
    def release_manifest_path(self) -> Path | None:
        return self._release_manifest_path

    @property
    def skill_authority(self) -> SkillAuthority | None:
        return self._skill_authority

    @property
    def omni_grant_authority(self) -> OmniGrantAuthority:
        return self._omni_grants

    # ------------------------------------------------------------------
    # D-14 澄清不是确认（最小集）
    #
    # NEEDS_CLARIFICATION 不是确认通道：澄清发生在任何 effect 之前，只写
    # 未决问题（clarification_questions 台账）；用户答复创建 generation+1
    # （旧 generation fence 被 supersede，新 generation 重新检索/规划/冻结/
    # 算风险 —— 全部由既有按 generation 隔离的流水线承担）；澄清答复本身
    # 不是副作用凭证（不产生任何 effect/fact/grant）。
    # ------------------------------------------------------------------

    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"

    def pause_for_clarification(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        question: str,
        now_ms: int,
    ) -> dict:
        """登记 effect 前的澄清未决问题并返回 NEEDS_CLARIFICATION 形态。

        store 层硬约束：该 (request_id, run_id, generation) 已有任何 effect
        head 时拒绝（澄清不得发生在副作用之后）。
        """
        record = self._store.record_clarification_question(
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            question=question,
            now_ms=now_ms,
        )
        return {
            "outcome": self.NEEDS_CLARIFICATION,
            "question_id": record["question_id"],
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
            "question": record["question"],
            "state": record["state"],
            "side_effect_credential": False,
        }

    def resume_from_clarification(
        self,
        *,
        question_id: str,
        lease_id: str,
        owner_instance_id: str | None = None,
        answered_at_ms: int,
        lease_duration_ms: int = 60_000,
    ) -> dict:
        """登记澄清答复并翻到 generation+1（旧 generation fence 被 supersede）。

        答复只改澄清台账 + generation 租约，不写任何 effect/fact —— 答复
        本身不是副作用凭证；新 generation 的检索/规划/冻结/风险由流水线重跑。
        """
        answered = self._store.answer_clarification_question(
            question_id=question_id,
            answered_at_ms=answered_at_ms,
        )
        current = self._store.get_generation(answered["request_id"])
        if current is None:
            raise OrchestrationError("clarification.request_generation_missing")
        if current.generation != answered["generation"]:
            raise OrchestrationError("clarification.generation_already_advanced")
        view, _ = self._store.acquire_generation_lease(
            request_id=answered["request_id"],
            run_id=current.run_id,
            run_sequence=current.run_sequence,
            generation=current.generation + 1,
            gateway_epoch=current.gateway_epoch,
            lease_id=lease_id,
            owner_instance_id=owner_instance_id or self._instance_id,
            issued_at_ms=answered_at_ms,
            lease_duration_ms=lease_duration_ms,
        )
        return {
            "outcome": "CLARIFICATION_ANSWERED",
            "question_id": question_id,
            "request_id": answered["request_id"],
            "run_id": view.run_id,
            "previous_generation": answered["generation"],
            "generation": view.generation,
            "side_effect_credential": False,
        }

    def delivery_trust_bundle(self, now_ms: int):
        return self._authority.delivery_trust_bundle(
            gateway_epoch=self._epoch,
            now_ms=now_ms,
        )

    def status_payload(self) -> dict[str, object]:
        with self._last_error_lock:
            error = self._last_error
            processed = self._processed_count
        return {
            "configured": True,
            "running": self._thread is not None and self._thread.is_alive(),
            "processed_count": processed,
            "last_error_code": error,
            "capability_manifest_sha256": compatibility_capability_manifest(
                self._components.manifest_sha256,
                generated_at_ms=self._components.generated_at_ms,
            ).sha256,
        }

    @contextmanager
    def authorize_life_capability_action(
        self,
        *,
        life_id: str,
        artifact_id: str,
        artifact_sha256: str,
        execution_id: str,
        step_id: str,
        action_id: str,
        arguments: Mapping[str, Any],
        source_inquiry_id: str = "",
        autonomous_intent_id: str = "",
    ) -> Iterator[dict[str, object]]:
        """Issue the outer ticket required before a learned artifact can act.

        Learned skills are immutable Life artifacts, not release catalog
        entries.  They still must enter the same Gateway policy and signed
        authority chain as a normal run: this method issues a short-lived
        *outer* execution ticket, records its evidence, registers it with the
        Omni grant authority, and exposes only the bound run context to the
        embedded backend.  The backend then obtains its per-action Omni grant
        through the existing internal API.
        """
        if (
            not all(isinstance(value, str) and value for value in (
                life_id, artifact_id, artifact_sha256, execution_id, step_id, action_id,
            ))
            or not isinstance(arguments, Mapping)
            or action_id != "omni_body"
        ):
            raise OrchestrationError("life_capability.authority_input_invalid")
        if len(artifact_sha256) != 64 or any(char not in "0123456789abcdef" for char in artifact_sha256):
            raise OrchestrationError("life_capability.artifact_digest_invalid")

        now_ms = time.time_ns() // 1_000_000
        invocation_sha256 = canonical_sha256({
            "action_id": action_id,
            "arguments": dict(arguments),
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha256,
            "execution_id": execution_id,
            "step_id": step_id,
        })
        # Continuity contracts reserve the ``req_`` / ``run_`` opaque ID
        # namespaces.  Keep the learned-artifact execution deterministic, but
        # use those canonical prefixes so the Life capsule can validate it.
        request_id = "req_" + canonical_sha256({
            "execution_id": execution_id,
            "step_id": step_id,
            "invocation": invocation_sha256,
        })
        run_id = "run_" + canonical_sha256({"request_id": request_id, "life_id": life_id})
        conversation_scope_hash = canonical_sha256({
            "domain": "tiangong.gateway.learned-capability-conversation.v1",
            "artifact_id": artifact_id,
            "life_id": life_id,
        })
        # This must be exactly the same scope that the Life atomic-context
        # endpoint binds into its authorization.  The artifact is separately
        # bound in the life evidence and ticket fields below; changing the
        # principal derivation here would create two authorities for one run.
        principal_scope_hash = canonical_sha256({
            "domain": "tiangong.gateway.life-principal-scope.v1",
            "tenant_id": "desktop",
            "link_account_id": "desktop-local",
            "conversation_scope_hash": conversation_scope_hash,
        })
        profile = LifeProfileBindings(user_callsign="life-capability")
        current_request = f"Execute learned capability {artifact_id} step {step_id}"
        try:
            transport = self._life_transport_for_execution()
            life = LifeClient(transport, self._objects).compile_and_authorize_snapshot(
                request_id=request_id,
                run_id=run_id,
                generation=0,
                current_request=current_request,
                tenant_id="desktop",
                link_account_id="desktop-local",
                conversation_scope_hash=conversation_scope_hash,
                profile=profile,
                observed_at_ms=now_ms,
            )
        except Exception as exc:
            detail = str(getattr(exc, "code", "") or type(exc).__name__).replace(" ", "_")[:96]
            raise OrchestrationError(f"life_capability.life_snapshot_unavailable:{detail}") from exc
        if life.snapshot.identity_ref != life_id:
            raise OrchestrationError("life_capability.life_identity_mismatch")

        manifest = compatibility_capability_manifest(
            self._components.manifest_sha256,
            generated_at_ms=now_ms,
        )
        outer_registry = compatibility_action_registry(manifest, generated_at_ms=now_ms)
        permission = outer_registry.permissions[0]
        resources = ResourceEnvelope(
            max_runtime_ms=60_000,
            max_output_bytes=16 * 1024 * 1024,
            max_tool_calls=1,
        )
        life_evidence_payload = {
            "schema": "tiangong.gateway.learned-capability-life-evidence.v1",
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha256,
            "execution_id": execution_id,
            "life_id": life_id,
            "life_snapshot_id": life.snapshot.snapshot_id,
            "life_snapshot_revision": life.snapshot.revision,
            "life_snapshot_sha256": life.snapshot.sha256,
            "step_id": step_id,
            "observed_at_ms": now_ms,
        }
        if source_inquiry_id:
            life_evidence_payload.update({
                "schema": "tiangong.gateway.world-inquiry-life-evidence.v1",
                "source_inquiry_id": source_inquiry_id,
                "autonomous_intent_id": autonomous_intent_id,
            })
        life_evidence_ref = "lev_" + canonical_sha256(life_evidence_payload)
        self._policy_evidence.record("life-event", life_evidence_ref, life_evidence_payload)
        # D-08: a learned capability acts under the Life chain's preauthorized
        # facts only; tool/web text met along the way can never authorize it.
        authorization_source_refs = (
            SourceRef(
                source_type="PREAUTHORIZED_USER_FACT",
                object_id=life_evidence_ref,
                object_revision=1,
                sha256=life_evidence_ref[4:],
            ),
        )
        provenance_source_refs = authorization_source_refs
        if source_inquiry_id:
            provenance_source_refs = tuple(sorted((
                *authorization_source_refs,
                SourceRef(
                    source_type="EXTERNAL_DATA",
                    object_id=source_inquiry_id,
                    object_revision=1,
                    sha256=artifact_sha256,
                ),
            ), key=lambda ref: ref.sort_key()))
        # D-09: derive impact from the learned step's normalized arguments and
        # its evaluation-time target state; deterministic, floors can only rise.
        step_action = str(arguments.get("action") or action_id)
        step_target = str(arguments.get("target") or "")
        step_args = arguments.get("args") if isinstance(arguments.get("args"), Mapping) else arguments
        step_target_state = probe_target_state(step_target, self._workspace_root)
        step_target_snapshot_sha256 = canonical_sha256(step_target_state) if step_target_state else None
        step_target_ref = (
            "target-" + canonical_sha256({"action": step_action, "target": step_target}) if step_target else None
        )
        knobs = derive_impact_knobs(
            step_action,
            step_args,
            target=step_target,
            target_state=step_target_state,
            workspace_root=str(self._workspace_root),
        )
        intent = ActionIntent(
            intent_id="intent-" + canonical_sha256({
                "artifact_id": artifact_id,
                "invocation_sha256": invocation_sha256,
                "life_evidence_ref": life_evidence_ref,
            }),
            source="life_scheduler",
            life_id=life_id,
            principal_scope_hash=principal_scope_hash,
            conversation_scope_hash=conversation_scope_hash,
            request_id=request_id,
            run_id=run_id,
            generation=0,
            action_id=permission.action_id,
            action_version=permission.action_version,
            arguments_sha256=invocation_sha256,
            workspace_id="workspace-" + canonical_sha256(str(self._workspace_root)),
            workspace_scope_hash=self._omni_grants.workspace_scope_hash,
            input_object_refs=(),
            requested_side_effects=permission.allowed_side_effects,
            requested_resources=resources,
            source_refs=provenance_source_refs,
            payload_sha256=invocation_sha256,
            target_ref=step_target_ref,
            target_snapshot_sha256=step_target_snapshot_sha256,
            life_snapshot_revision=life.snapshot.revision,
            life_snapshot_sha256=life.snapshot.sha256,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + 60_000,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        impact = compute_action_impact(
            intent,
            permission,
            affected_internal_nodes=("node_learned_capability_runtime",),
            external_recipient_count=knobs["external_recipient_count"],
            credential_scope_milli=knobs["credential_scope_milli"],
            privacy_scope_milli=knobs["privacy_scope_milli"],
            blast_radius_milli=knobs["blast_radius_milli"],
            irreversibility_milli=knobs["irreversibility_milli"],
            uncertainty_milli=knobs["uncertainty_milli"],
            target_snapshot_sha256=step_target_snapshot_sha256,
            created_at_ms=now_ms,
        )
        policy_snapshot_sha256 = canonical_sha256({
            "policy": "tiangong.gateway.learned-capability.outer-ticket.v1",
            "registry_sha256": outer_registry.registry_sha256,
        })
        policy_engine = PolicyEngine(
            outer_registry,
            policy_snapshot_sha256=policy_snapshot_sha256,
            skill_catalog_hash=self._release_manifest.skill_catalog_sha256,
            capability_manifest_hash=manifest.sha256,
            component_manifest_hash=self._components.manifest_sha256,
        )
        if source_inquiry_id:
            # P13.2 must traverse the already-existing Life proposal emitter.
            # The in-process transport is the current Gateway itself: it owns
            # Policy evaluation and returns only a receipt, never execution
            # authority. Ticket/Grant issuance remains below this boundary.
            from life_service.action_intents import ActionIntentReceipt, LifeActionIntentEmitter

            class _GatewayPolicyTransport:
                decision = None

                def submit(self, proposed: ActionIntent) -> ActionIntentReceipt:
                    self.decision = policy_engine.evaluate(
                        proposed,
                        impact,
                        decided_at_ms=now_ms,
                        authorization_source_refs=authorization_source_refs,
                    )
                    status = {
                        "ALLOW": "AUTHORIZED",
                        "REQUIRE_CONFIRMATION": "CONFIRMATION_REQUIRED",
                        "REJECT": "REJECTED",
                    }[self.decision.outcome]
                    receipt = ActionIntentReceipt(
                        proposed.intent_id,
                        proposed.intent_sha256,
                        status,
                        self.decision.decision_id,
                        "",
                    )
                    return ActionIntentReceipt(
                        receipt.intent_id,
                        receipt.intent_sha256,
                        receipt.status,
                        receipt.policy_decision_id,
                        receipt.computed_sha256(),
                    )

            transport = _GatewayPolicyTransport()
            LifeActionIntentEmitter(transport).submit_self_will(
                intent,
                source_inquiry_id=source_inquiry_id,
                source_inquiry_sha256=artifact_sha256,
            )
            decision = transport.decision
            assert decision is not None
        else:
            decision = policy_engine.evaluate(
                intent,
                impact,
                decided_at_ms=now_ms,
                authorization_source_refs=authorization_source_refs,
            )
        if decision.outcome != "ALLOW":
            self._policy_evidence.record_evaluation(
                intent=intent, impact=impact, permission=permission, registry=outer_registry,
                decision=decision, ticket=None, grant=None, observed_at_ms=now_ms,
            )
            raise OrchestrationError("life_capability.outer_policy_rejected")

        effect_id = "eff_" + canonical_sha256({
            "artifact_id": artifact_id,
            "execution_id": execution_id,
            "step_id": step_id,
            "intent": intent.intent_sha256,
        })
        ticket_payload = ExecutionTicketPayload(
            ticket_id="execution-ticket-" + canonical_sha256({"effect_id": effect_id, "decision": decision.decision_sha256}),
            nonce="execution-nonce-" + canonical_sha256({"effect_id": effect_id, "random": invocation_sha256})[:40],
            issued_at_ms=now_ms,
            not_before_ms=now_ms,
            expires_at_ms=now_ms + 60_000,
            gateway_epoch=self._epoch,
            request_id=request_id,
            run_id=run_id,
            generation=0,
            effect_id=effect_id,
            channel="system",
            tenant_id="desktop",
            link_account_id="desktop-local",
            conversation_scope_hash=conversation_scope_hash,
            principal_scope_hash=principal_scope_hash,
            capability_manifest_hash=manifest.sha256,
            policy_snapshot_hash=decision.policy_snapshot_sha256,
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            component_manifest_hash=self._components.manifest_sha256,
            life_snapshot_revision=life.snapshot.revision,
            life_snapshot_hash=life.snapshot.sha256,
            risk_class=decision.computed_risk,
            action_id=permission.action_id,
            action_version=permission.action_version,
            argument_schema_sha256=manifest.actions[0].argument_schema_sha256,
            arguments_hash=invocation_sha256,
            workspace_id="workspace-" + canonical_sha256(str(self._workspace_root)),
            input_objects=(),
            object_grants_sha256=canonical_sha256([]),
            output_root_id="output-" + canonical_sha256({"execution_id": execution_id, "step_id": step_id}),
            artifact_intent_id="artifact-" + canonical_sha256({"artifact_id": artifact_id, "artifact_sha256": artifact_sha256}),
            max_output_bytes=resources.max_output_bytes,
            max_runtime_ms=resources.max_runtime_ms,
            max_tool_calls=resources.max_tool_calls,
            resource_envelope_sha256=resources.sha256(),
            allowed_side_effects=permission.allowed_side_effects,
            side_effect_envelope_sha256=canonical_sha256({"allowed_side_effects": list(permission.allowed_side_effects)}),
        )
        ticket = self._authority.execution_signer.sign_execution(ticket_payload)
        self._policy_evidence.record_evaluation(
            intent=intent, impact=impact, permission=permission, registry=outer_registry,
            decision=decision, ticket=ticket, grant=None, observed_at_ms=now_ms,
        )
        self._omni_grants.register(
            ticket,
            life_id=life_id,
            life_evidence_ref=life_evidence_ref,
            session_id=f"learned-capability:{artifact_id}",
            registered_at_ms=now_ms,
            authority_expires_at_ms=ticket_payload.expires_at_ms,
        )
        try:
            yield {
                "execution_ticket_id": ticket_payload.ticket_id,
                "request_id": request_id,
                "run_id": run_id,
                "generation": 0,
                "principal_scope_hash": principal_scope_hash,
                "workspace_id": ticket_payload.workspace_id,
                "gateway_url": self._gateway_url,
                "session_id": f"learned-capability:{artifact_id}",
                "life_id": life_id,
                "artifact_id": artifact_id,
                "artifact_sha256": artifact_sha256,
                "source_inquiry_id": source_inquiry_id,
                "autonomous_intent_id": autonomous_intent_id,
            }
        finally:
            self._omni_grants.unregister(ticket_payload.ticket_id)

    @staticmethod
    def _world_observation(arguments: object) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            raise OrchestrationError("world_inquiry.observation_missing")
        action = str(arguments.get("action") or "").strip()
        repository_aliases = {
            "repository.status": "git.status",
            "repository.head": "git.log",
            "repository.diff": "git.diff",
            "repository.read_source_window": "file.read",
        }
        action = repository_aliases.get(action, action)
        allowed = {
            "system.health", "system.capabilities", "file.read", "file.list",
            "file.search", "file.hash", "git.status", "git.diff", "git.log",
            "web.search", "web.fetch",
        }
        if action not in allowed:
            raise OrchestrationError("world_inquiry.observation_not_read_only")
        target = str(arguments.get("target") or "").strip()
        args = arguments.get("args") if isinstance(arguments.get("args"), Mapping) else {}
        args = dict(args)
        if str(arguments.get("action") or "").strip() == "repository.head":
            args["limit"] = 1
        if len(canonical_json_bytes({"action": action, "target": target, "args": args})) > 64 * 1024:
            raise OrchestrationError("world_inquiry.observation_too_large")
        return {"action": action, "target": target, "args": args}

    def _dispatch_next_world_inquiry(self) -> bool:
        try:
            inquiry, sink = self._world_inquiries.get_nowait()
        except queue.Empty:
            return False
        now_ms = time.time_ns() // 1_000_000
        try:
            client = self._backend_compat_client
            if client is None:
                raise OrchestrationError("world_inquiry.backend_unavailable")
            status, payload, _ = client.request(
                "POST",
                "/api/v1/internal/world-inquiry/decision",
                {"inquiry": inquiry.model_dump(mode="json")},
                timeout_seconds=240,
            )
            raw_decision = payload.get("decision") if isinstance(payload, Mapping) else None
            if status >= 400 or payload.get("ok") is not True or not isinstance(raw_decision, Mapping):
                raise OrchestrationError("world_inquiry.self_will_unavailable")
            adapter = ExistingSelfWillAdapter(lambda _inquiry: raw_decision)
            decision, autonomous = adapter.decide(inquiry, decided_at_ms=now_ms)
            sink({
                "phase": "DECIDED",
                "at_ms": now_ms,
                "decision": decision.decision,
                "decision_record": asdict(decision),
                "autonomous_intent": None if autonomous is None else asdict(autonomous),
            })
            if autonomous is None:
                phase = {"DEFER": "DEFERRED", "DISMISS": "DISMISSED", "EXPIRE": "EXPIRED"}[decision.decision]
                sink({"phase": phase, "at_ms": now_ms, "decision": decision.decision})
                return True
            arguments = self._world_observation(raw_decision.get("observation"))
            with self.authorize_life_capability_action(
                life_id=inquiry.scope.life_id,
                artifact_id=inquiry.inquiry_id,
                artifact_sha256=inquiry.inquiry_sha256,
                execution_id=autonomous.autonomous_intent_id,
                step_id="world-inquiry-observation",
                action_id="omni_body",
                arguments=arguments,
                source_inquiry_id=inquiry.inquiry_id,
                autonomous_intent_id=autonomous.autonomous_intent_id,
            ) as run_context:
                sink({
                    "phase": "STARTED",
                    "at_ms": time.time_ns() // 1_000_000,
                    "run_id": run_context["run_id"],
                    "execution_ticket_id": run_context["execution_ticket_id"],
                })
                invoke_status, invoke_payload, _ = client.request(
                    "POST",
                    "/api/v1/internal/life-action/invoke",
                    {"action_id": "omni_body", "arguments": arguments, "run_context": run_context},
                    timeout_seconds=300,
                )
                if invoke_status >= 400 or invoke_payload.get("ok") is False:
                    raise OrchestrationError("world_inquiry.observation_failed")
            return True
        except Exception as exc:
            sink({
                "phase": "FAILED",
                "at_ms": time.time_ns() // 1_000_000,
                "reason_code": self._safe_error_code(exc),
            })
            return True

    def _set_error(self, code: str | None) -> None:
        with self._last_error_lock:
            self._last_error = code

    @staticmethod
    def _safe_error_code(error: Exception) -> str:
        declared = getattr(error, "code", None)
        if isinstance(declared, str) and declared:
            return declared[:160]
        if isinstance(error, FileNotFoundError):
            filename = getattr(error, "filename", None)
            if isinstance(filename, str) and filename:
                leaf = Path(filename).name
                if leaf:
                    return f"unhandled.FileNotFoundError.{leaf}"[:160]
        frames = traceback.extract_tb(error.__traceback__)
        if not frames:
            return error.__class__.__name__[:160]
        frame = frames[-1]
        module = Path(frame.filename).stem
        function = frame.name
        safe = "".join(
            char if char.isascii() and (char.isalnum() or char in "._-") else "_"
            for char in f"unhandled.{error.__class__.__name__}.{module}.{function}.line_{frame.lineno}"
        )
        return safe[:160]

    def _run(self) -> None:
        while not self._closed.is_set():
            activation: ActiveRequestActivation | None = None
            try:
                now_ms = time.time_ns() // 1_000_000
                self._reconcile_stale_effects(now_ms=now_ms)
                stranded_cancelled = self._store.list_cancelled_active_session_request_ids(limit=1)
                if stranded_cancelled:
                    cancelled_request_id = stranded_cancelled[0]
                    cancelled_entry = self._store.get_request_entry(cancelled_request_id)
                    if cancelled_entry is None:
                        raise OrchestrationError("orchestration.cancelled_request.journal_missing")
                    self._store.complete_session_request(
                        cancelled_entry.session_scope_hash,
                        cancelled_request_id,
                        completed_at_ms=now_ms,
                        release_generation=False,
                    )
                    self._set_error(None)
                    continue
                recovered = self._activator.recover_next(now_ms=now_ms)
                if recovered is not None:
                    outboxes = self._store.list_outbox_for_request(
                        recovered.entry.request_id,
                        run_id=recovered.generation.run_id,
                        generation=recovered.generation.generation,
                    )
                    if not outboxes and (
                        os.environ.get("TIANGONG_REQUEST_REEXECUTION", "1").strip().lower()
                        not in {"0", "false", "off"}
                        and recovered.generation.revision <= 3
                    ):
                        # 崩溃于执行中（无 outbox）：重新执行而非直接判死。
                        # backend 的 simple_chain 按 request_id 恢复
                        #（checkpoint/frontier），effect 层幂等挡住重复
                        # 副作用；recover 每次 revision+1，超过 3 次按
                        # 循环防护判死。P18 regenerative 机器由此被真正消费。
                        reexec_stop = threading.Event()

                        def reexec_heartbeat() -> None:
                            consecutive_failures = 0
                            while not reexec_stop.wait(10.0):
                                try:
                                    self._activator.heartbeat(
                                        recovered,
                                        now_ms=time.time_ns() // 1_000_000,
                                    )
                                    consecutive_failures = 0
                                except Exception:
                                    consecutive_failures += 1
                                    if consecutive_failures >= 3:
                                        reexec_stop.set()

                        reexec_thread = threading.Thread(
                            target=reexec_heartbeat,
                            name="tiangong-request-reexecution-heartbeat",
                            daemon=True,
                        )
                        reexec_thread.start()
                        try:
                            self.process(recovered)
                        finally:
                            reexec_stop.set()
                            reexec_thread.join(timeout=5.0)
                        with self._last_error_lock:
                            self._processed_count += 1
                            self._last_error = None
                        self._set_error(None)
                        continue
                    if not outboxes:
                        self._finalize_unhandled(
                            recovered,
                            OrchestrationError("orchestration.restart.before_outbox"),
                        )
                    self._set_error(None)
                    continue
                if self._delivery_outbox.dispatch_next(now_ms=now_ms):
                    self._set_error(None)
                    continue
                activation = self._activator.claim_next(now_ms=now_ms)
                if activation is None:
                    if self._dispatch_next_world_inquiry():
                        self._set_error(None)
                        continue
                    self._closed.wait(self._poll_seconds)
                    continue
                heartbeat_stop = threading.Event()

                def maintain_generation() -> None:
                    # 心跳退避：单次失败（瞬时 DB 锁/系统打盹）不清零
                    # 直接放弃会让长执行在交付边界被整体废弃。连续 3 次
                    # 失败（约 30s 无有效心跳）才判定租约真正失联。
                    consecutive_failures = 0
                    while not heartbeat_stop.wait(10.0):
                        try:
                            self._activator.heartbeat(
                                activation,
                                now_ms=time.time_ns() // 1_000_000,
                            )
                            consecutive_failures = 0
                        except Exception as heartbeat_error:
                            consecutive_failures += 1
                            self._set_error(self._safe_error_code(heartbeat_error))
                            if consecutive_failures >= 3:
                                heartbeat_stop.set()

                heartbeat = threading.Thread(
                    target=maintain_generation,
                    name="tiangong-request-generation-heartbeat",
                    daemon=True,
                )
                heartbeat.start()
                try:
                    self.process(activation)
                finally:
                    heartbeat_stop.set()
                    heartbeat.join(timeout=5.0)
                with self._last_error_lock:
                    self._processed_count += 1
                    self._last_error = None
            except Exception as exc:
                primary_code = self._safe_error_code(exc)
                finalization_code: str | None = None
                if activation is not None:
                    try:
                        self._finalize_unhandled(activation, exc)
                    except Exception as finalization_exc:
                        finalization_code = self._safe_error_code(finalization_exc)
                        diagnostic_log(
                            "[ORCH-FINALIZE-FAIL] "
                            f"primary={primary_code} finalization={finalization_code} "
                            f"request_id={activation.entry.request_id}"
                        )
                self._set_error(
                    primary_code
                    if finalization_code is None
                    else f"{primary_code}|finalize:{finalization_code}"[:160]
                )
                self._closed.wait(1.0)

    def _reconcile_stale_effects(self, *, now_ms: int, stale_after_ms: int = 240_000) -> int:
        """Watchdog: force-stabilize effects stuck beyond the window as AMBIGUOUS.

        The legacy compatibility execution path synchronously waits for the
        frozen backend.  A result that never arrives (no success, no error)
        would otherwise wedge the single orchestration worker forever.  This
        guard turns such effects into AMBIGUOUS so the request can terminalize
        and the queue can drain; it never replays the side effect.
        """
        reconciled = 0
        stale_before_ms = now_ms - stale_after_ms
        for effect_id in self._store.list_stale_non_terminal_effect_ids(
            stale_before_ms=stale_before_ms
        ):
            effect = self._store.get_effect(effect_id)
            if effect is None or effect.state not in {"CLAIMED", "SIDE_EFFECT_STARTED"}:
                continue
            result = EffectResult(
                result_id="effect-result-" + effect_id[4:20],
                effect_id=effect_id,
                status="AMBIGUOUS",
                fact_id="fact-effect-" + effect_id[4:20],
                evidence_sha256=canonical_sha256(
                    {"code": "effect_execution_timeout_reconcile", "status": "AMBIGUOUS"}
                ),
                error_code="effect_execution_timeout_reconcile",
                observed_at_ms=now_ms,
                result_sha256="0" * 64,
            ).with_computed_sha256()
            try:
                self._store.complete_effect(result)
                reconciled += 1
            except Exception:
                continue
            # 统一事件契约补写（best-effort）：事件补写失败绝不影响回收计数。
            try:
                claim = getattr(effect, "claim", None)
                _append_orchestration_effect_event(
                    self._store,
                    event_key=f"step.ambiguous:{effect_id}",
                    event_type="step.ambiguous",
                    payload={"effect_state": "AMBIGUOUS", "source": "gateway_orchestration_stale_reap"},
                    request_id=str(getattr(claim, "request_id", "") or ""),
                    run_id=str(getattr(claim, "run_id", "") or ""),
                    generation=int(getattr(claim, "generation", 0) or 0),
                    effect_id=effect_id,
                    created_at_ms=now_ms,
                )
            except Exception:
                pass
        if reconciled:
            diagnostic_log(
                f"orchestration.watchdog.reconciled count={reconciled} at_ms={now_ms}"
            )
        return reconciled

    @staticmethod
    def _event_id(machine: str, entity_id: str, revision: int, to_state: str, evidence: str | None) -> str:
        return "evt_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.production-transition.v1",
                "entity_id": entity_id,
                "evidence": evidence,
                "machine": machine,
                "revision": revision,
                "to_state": to_state,
            }
        )

    def _advance(
        self,
        machine: str,
        entity_id: str,
        to_state: str,
        *,
        now_ms: int,
        fact_id: str | None = None,
        evidence_sha256: str | None = None,
        outbox: tuple[OutboxIntent, ...] = (),
    ):
        snapshot = self._store.get_snapshot(machine, entity_id)
        if snapshot is None:
            raise OrchestrationError("orchestration.state.missing")
        if snapshot.state == to_state:
            return snapshot
        event = TransitionEvent(
            event_id=self._event_id(machine, entity_id, snapshot.revision, to_state, evidence_sha256),
            event_type=f"orchestration.{machine}.{to_state.lower()}",
            source_component_id="tiangong-total-gateway",
            machine=machine,
            entity_id=entity_id,
            request_id=snapshot.request_id,
            run_id=snapshot.run_id,
            generation=snapshot.generation,
            expected_revision=snapshot.revision,
            to_state=to_state,
            occurred_at_ms=max(now_ms, snapshot.updated_at_ms),
            fact_id=fact_id,
            evidence_sha256=evidence_sha256,
            side_effect_started=to_state in {"RUNNING", "SENDING", "AMBIGUOUS", "RECONCILE_REQUIRED"},
            event_sha256="0" * 64,
        ).with_computed_event_sha256()
        result = self._store.apply_event_with_outbox(event, outbox, recorded_at_ms=event.occurred_at_ms)
        if not result.decision.accepted:
            raise OrchestrationError(result.decision.reason_code)
        return result.decision.current

    def _initialize(self, machine: str, entity_id: str, activation: ActiveRequestActivation, now_ms: int) -> None:
        snapshot = new_state_snapshot(
            machine,
            entity_id=entity_id,
            request_id=activation.entry.request_id,
            run_id=activation.generation.run_id,
            generation=activation.generation.generation,
            created_at_ms=now_ms,
        )
        self._store.initialize_snapshot(snapshot)

    def _commit_life_execution(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        life_id: str,
        session_scope_hash: str,
        principal_scope_hash: str,
        workspace_id: str,
        user_goal: str,
        final_result: str,
        fact_ids: tuple[str, ...],
        completed_at_ms: int,
    ) -> Mapping[str, Any] | None:
        commit = self._life_execution_commit
        if commit is None:
            return None
        repository_evidence = None
        provider = self._repository_evidence_provider
        if callable(provider):
            try:
                candidate = provider({
                    "life_id": life_id,
                    "principal_scope_hash": principal_scope_hash,
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "request_id": request_id,
                })
                if isinstance(candidate, Mapping):
                    encoded = canonical_json_bytes(candidate)
                    if len(encoded) <= 64 * 1024:
                        repository_evidence = dict(candidate)
            except Exception:
                # Repository evidence enriches the terminal experience but may
                # never block the authoritative Life outcome commit.
                repository_evidence = None
        payload = {
            "schema": "tiangong.life.execution-terminal.v1",
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
            "life_id": life_id,
            "session_scope_hash": session_scope_hash,
            "status": "completed",
            "user_goal_sha256": canonical_sha256(user_goal),
            "final_result_sha256": canonical_sha256(final_result),
            "fact_ids": list(sorted(set(fact_ids))),
            "repository_evidence": repository_evidence,
            "completed_at_ms": completed_at_ms,
        }
        try:
            result = commit(payload)
        except Exception as exc:
            diagnostic_log(
                "[ORCH-LIFE-COMMIT-FAIL] "
                f"request_id={request_id} run_id={run_id} "
                f"type={type(exc).__name__} message={str(exc)[:500]}"
            )
            raise OrchestrationError("orchestration.life.commit_failed") from exc
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise OrchestrationError("orchestration.life.commit_failed")
        return result

    def _acquire_life_snapshot(
        self,
        envelope,
        profile: LifeProfileBindings,
        activation: ActiveRequestActivation | None = None,
        now_ms: int | None = None,
        current_context_tokens: int | None = None,
    ):
        transport = self._life_transport_for_execution()
        client = LifeClient(transport, self._objects)
        if activation is None or now_ms is None:
            return client.acquire_snapshot(
                tenant_id=envelope.tenant_id,
                link_account_id=envelope.link_account_id,
                conversation_scope_hash=envelope.conversation_scope_hash,
                profile=profile,
            )
        return client.compile_and_authorize_snapshot(
            request_id=activation.entry.request_id,
            run_id=activation.generation.run_id,
            generation=activation.generation.generation,
            current_request=envelope.text,
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            profile=profile,
            observed_at_ms=now_ms,
            current_context_tokens=current_context_tokens,
        )

    def _finalize_unhandled(
        self,
        activation: ActiveRequestActivation,
        error: Exception,
    ) -> None:
        diagnostic_log(
            f"[UNHANDLED] code={getattr(error, 'code', error.__class__.__name__)} "
            f"msg={str(error)[:200]}"
        )
        request_id = activation.entry.request_id
        run_id = activation.generation.run_id
        generation = activation.generation.generation
        request = self._store.get_snapshot("request", request_id)
        if request is None:
            return
        if self._store.list_outbox_for_request(
            request_id,
            run_id=run_id,
            generation=generation,
        ):
            # Once delivery intent is committed, the Outbox worker owns both
            # the external boundary and finalization.  A late exception in the
            # assembly stack must not cancel or release that durable intent.
            return
        if request.is_terminal:
            self._store.complete_session_request(
                activation.entry.session_scope_hash,
                request_id,
                completed_at_ms=max(time.time_ns() // 1_000_000, request.updated_at_ms),
                release_generation=True,
            )
            return
        observed_at_ms = max(time.time_ns() // 1_000_000, request.updated_at_ms)
        code = str(getattr(error, "code", None) or error.__class__.__name__)[:160]
        evidence = canonical_sha256(
            {
                "code": code,
                "domain": "tiangong.gateway.orchestration-unhandled.v1",
                "generation": generation,
                "request_id": request_id,
                "run_id": run_id,
            }
        )
        fact_id = "fact-orchestration-" + evidence[:32]
        effects = self._store.list_effects_for_request(
            request_id,
            run_id=run_id,
            generation=generation,
        )
        execution = self._store.get_snapshot("execution", "execution-" + run_id)
        if execution is not None and not execution.is_terminal:
            crossed = any(
                item.claim.effect_kind == "execution"
                and (
                    item.state == "SIDE_EFFECT_STARTED"
                    or (
                        item.state == "AMBIGUOUS"
                        and str(item.claim.owner_component_id) != "tiangong-backend"
                    )
                )
                for item in effects
            )
            if crossed:
                if execution.state not in {"AMBIGUOUS", "RECONCILE_REQUIRED"}:
                    self._advance(
                        "execution",
                        execution.entity_id,
                        "AMBIGUOUS",
                        now_ms=observed_at_ms,
                        fact_id=fact_id,
                        evidence_sha256=evidence,
                    )
                self._advance(
                    "execution",
                    execution.entity_id,
                    "RECONCILE_REQUIRED",
                    now_ms=observed_at_ms,
                    fact_id=fact_id,
                    evidence_sha256=evidence,
                )
            elif execution.state != "NOT_STARTED":
                self._advance(
                    "execution",
                    execution.entity_id,
                    "FAILED_FINAL",
                    now_ms=observed_at_ms,
                    fact_id=fact_id,
                    evidence_sha256=evidence,
                )
            else:
                self._advance(
                    "execution",
                    execution.entity_id,
                    "CANCELLED",
                    now_ms=observed_at_ms,
                )
        delivery = self._store.get_snapshot("delivery", "delivery-" + run_id)
        if delivery is not None and not delivery.is_terminal:
            if delivery.state in {"SENDING", "UPLOADING", "CHANNEL_ACCEPTED", "AMBIGUOUS", "RECONCILE_REQUIRED"}:
                if delivery.state not in {"AMBIGUOUS", "RECONCILE_REQUIRED"}:
                    self._advance(
                        "delivery",
                        delivery.entity_id,
                        "AMBIGUOUS",
                        now_ms=observed_at_ms,
                        fact_id=fact_id,
                        evidence_sha256=evidence,
                    )
                self._advance(
                    "delivery",
                    delivery.entity_id,
                    "RECONCILE_REQUIRED",
                    now_ms=observed_at_ms,
                    fact_id=fact_id,
                    evidence_sha256=evidence,
                )
            elif delivery.state != "NOT_PLANNED":
                self._advance(
                    "delivery",
                    delivery.entity_id,
                    "FAILED_FINAL",
                    now_ms=observed_at_ms,
                    fact_id=fact_id,
                    evidence_sha256=evidence,
                )
            else:
                self._advance(
                    "delivery",
                    delivery.entity_id,
                    "CANCELLED",
                    now_ms=observed_at_ms,
                )
        self._advance(
            "request",
            request_id,
            "FAILED",
            now_ms=observed_at_ms,
            fact_id=fact_id,
            evidence_sha256=evidence,
        )
        self._persist_interruption(
            activation,
            reason_code=code,
            observed_at_ms=observed_at_ms,
            fact_id=fact_id,
        )
        self._store.complete_session_request(
            activation.entry.session_scope_hash,
            request_id,
            completed_at_ms=observed_at_ms,
            release_generation=True,
        )

    def _persist_interruption(
        self,
        activation: ActiveRequestActivation,
        *,
        reason_code: str,
        observed_at_ms: int,
        fact_id: str,
    ) -> None:
        pending = tuple(
            sorted(
                item.claim.effect_id
                for item in self._store.list_effects_for_request(
                    activation.entry.request_id,
                    run_id=activation.generation.run_id,
                    generation=activation.generation.generation,
                )
                if item.state in {"CLAIMED", "SIDE_EFFECT_STARTED", "AMBIGUOUS"}
            )
        )
        persist_interruption_checkpoint(
            self._store,
            request_id=activation.entry.request_id,
            run_id=activation.generation.run_id,
            generation=activation.generation.generation,
            latest_safe_step=f"execution interrupted after durable fact {fact_id}",
            next_step=f"reconcile interruption {reason_code} before retrying effects",
            verified_fact_ids=(fact_id,),
            pending_effect_ids=pending,
            recovery_preconditions=(
                "verify every pending effect before any retry",
                "reload the latest workspace checkpoint and hashes",
            ),
            created_at_ms=observed_at_ms,
        )

    def _dispatch_repair_directive(
        self,
        *,
        directive,
        activation,
        envelope,
        manifest,
        action,
        permission,
        outer_registry,
        transport,
        arguments,
        grants,
        resources,
        life,
        life_evidence_ref,
        workspace_id,
        output_root_id,
        artifact_intent_id,
        request_id,
        run_id,
        run_sequence,
        generation,
        artifact_manifests,
    ):
        """M5 Final #2: execute a RepairDirective through the EXISTING runtime.

        Same authorities as the primary execution: PolicyEngine decision,
        RuntimeTicketAuthority signature, BackendClient runtime, and the
        ArtifactGate/QC pipeline. This is a bridge, not a second runtime
        — every reality change still lands in the one EffectLedger.
        Returns a RepairDispatchResult; runtime failures are returned as
        outcomes (never raised) so the repair loop can record them.
        """
        from total_gateway.verification_repair_coordinator import (
            RepairDispatchResult,
        )

        issued_at = time.time_ns() // 1_000_000
        repair_arguments = dict(arguments)
        repair_arguments["repair_goal"] = {
            "repair_goal_kind": directive.repair_goal_kind,
            "plan_entry_id": directive.plan_entry_id,
            "predicate_id": directive.predicate_id,
            "allowed_target_refs": list(directive.allowed_target_refs),
            "repair_constraints": list(directive.repair_constraints),
            "execution_budget_ms": directive.execution_budget_ms,
        }
        arguments_hash = canonical_sha256(repair_arguments)
        effect_intent = canonical_sha256(
            {
                "action_id": action.action_id,
                "action_version": action.version,
                "arguments_hash": arguments_hash,
                "capability_manifest_hash": manifest.sha256,
                "life_snapshot_hash": life.snapshot.sha256,
                "repair_directive_id": directive.repair_directive_id,
            }
        )
        repair_effect = derive_effect_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation.generation,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=effect_intent,
        )
        # Final P0-1: the Store dispatch boundary is claimed BEFORE the
        # effect ledger — atomically deciding whether THIS worker may
        # cross into the runtime for this (plan entry, attempt).
        reserved = self._store.reserve_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            repair_directive_sha256=directive.directive_sha256,
            plan_entry_id=directive.plan_entry_id,
            repair_attempt_no=directive.repair_attempt_no,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            effect_id=repair_effect.effect_id,
            effect_intent_sha256=effect_intent,
            reserved_at_ms=issued_at,
            # invocation-scoped, never the gateway instance id — two
            # threads in one process still single-flight
            dispatch_claim_id=uuid.uuid4().hex,
            claim_expires_at_ms=issued_at + 120_000,
        )
        if reserved["outcome"] != "EXECUTE":
            # Another coordinator owns this attempt — this worker must
            # not execute the runtime at all.
            return RepairDispatchResult(
                execution_outcome="ALREADY_CLAIMED",
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(),
            )
        claim = EffectClaim(
            effect_id=repair_effect.effect_id,
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation.generation,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=effect_intent,
            owner_component_id="tiangong-backend",
            claimed_at_ms=issued_at,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        existing_effect, created = self._store.claim_effect(claim)
        if not created and existing_effect.result is not None:
            # The effect ledger is already terminal; the binding (not
            # this branch) owns crash recovery — surface the binding
            # state so the caller never re-executes the runtime.
            state = str(reserved["binding"]["state"])
            if state == "SUCCEEDED":
                return RepairDispatchResult(
                    execution_outcome="DISPATCHED",
                    produced_subject_identity=str(
                        reserved["binding"]["produced_subject_identity"]
                    ),
                    execution_effect_ids=(repair_effect.effect_id,),
                )
            return RepairDispatchResult(
                execution_outcome=(
                    "EXECUTION_AMBIGUOUS"
                    if state in ("SIDE_EFFECT_STARTED", "AMBIGUOUS")
                    else "EXECUTION_FAILED"
                ),
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(repair_effect.effect_id,),
            )

        authorization_source_refs = tuple(
            sorted(
                (
                    SourceRef(
                        source_type="CURRENT_USER_INSTRUCTION",
                        object_id=request_id,
                        object_revision=1,
                        sha256=canonical_sha256(envelope.text or ""),
                    ),
                    SourceRef(
                        source_type="PREAUTHORIZED_USER_FACT",
                        object_id=life_evidence_ref,
                        object_revision=1,
                        sha256=life_evidence_ref[4:],
                    ),
                ),
                key=lambda ref: ref.sort_key(),
            )
        )
        intent = ActionIntent(
            intent_id="intent-repair-" + canonical_sha256(
                {
                    "repair_directive_id": directive.repair_directive_id,
                    "effect_id": repair_effect.effect_id,
                    "arguments_sha256": arguments_hash,
                    "created_at_ms": issued_at,
                }
            ),
            source="chat",
            life_id=life.snapshot.identity_ref,
            principal_scope_hash=envelope.principal_scope_hash,
            conversation_scope_hash=envelope.conversation_scope_hash,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            action_id=action.action_id,
            action_version=action.version,
            arguments_sha256=arguments_hash,
            workspace_id=workspace_id,
            workspace_scope_hash=self._omni_grants.workspace_scope_hash,
            input_object_refs=tuple(sorted(item.object_id for item in grants)),
            requested_side_effects=permission.allowed_side_effects,
            requested_resources=resources,
            source_refs=authorization_source_refs,
            payload_sha256=arguments_hash,
            attachment_set_sha256=canonical_sha256(
                [
                    {"object_id": item.object_id, "revision": item.revision, "sha256": item.sha256}
                    for item in grants
                ]
            ),
            life_snapshot_revision=life.snapshot.revision,
            life_snapshot_sha256=life.snapshot.sha256,
            created_at_ms=issued_at,
            expires_at_ms=issued_at + 60_000,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        knobs = derive_impact_knobs(
            action.action_id,
            None,
            scan_args=False,
            external_content_count=0,
        )
        impact = compute_action_impact(
            intent,
            permission,
            affected_internal_nodes=("node_tiangong_backend_model_runtime",),
            external_recipient_count=knobs["external_recipient_count"],
            credential_scope_milli=knobs["credential_scope_milli"],
            privacy_scope_milli=knobs["privacy_scope_milli"],
            blast_radius_milli=knobs["blast_radius_milli"],
            irreversibility_milli=knobs["irreversibility_milli"],
            uncertainty_milli=knobs["uncertainty_milli"],
            created_at_ms=issued_at,
        )
        policy_snapshot_sha256 = canonical_sha256(
            {
                "policy": "tiangong.gateway.model-run.autonomous-a0-a4.a5-deny.v3",
                "registry_sha256": outer_registry.registry_sha256,
            }
        )
        decision = PolicyEngine(
            outer_registry,
            policy_snapshot_sha256=policy_snapshot_sha256,
            skill_catalog_hash=self._release_manifest.skill_catalog_sha256,
            capability_manifest_hash=manifest.sha256,
            component_manifest_hash=self._components.manifest_sha256,
        ).evaluate(
            intent,
            impact,
            decided_at_ms=issued_at,
            authorization_source_refs=authorization_source_refs,
        )
        if decision.outcome != "ALLOW":
            self._policy_evidence.record_evaluation(
                intent=intent,
                impact=impact,
                permission=permission,
                registry=outer_registry,
                decision=decision,
                ticket=None,
                grant=None,
                observed_at_ms=issued_at,
            )
            # Definite non-execution: ONE atomic transition puts BOTH
            # authorities (binding + EffectLedger) into FAILED_FINAL, so
            # the RepairAttempt(EXECUTION_FAILED) can persist and the
            # runtime callback count stays zero.
            self._store.complete_repair_execution(
                repair_directive_id=directive.repair_directive_id,
                state="FAILED_FINAL",
                produced_subject_identity="",
                produced_subject_kind=directive.subject_kind,
                runtime_result_ref="policy-rejected",
                completed_at_ms=issued_at,
                error_code="repair.policy_denied",
            )
            return RepairDispatchResult(
                execution_outcome="EXECUTION_FAILED",
                produced_subject_identity=directive.effective_subject_identity,
                execution_effect_ids=(repair_effect.effect_id,),
            )
        payload = ExecutionTicketPayload(
            ticket_id="execution-ticket-" + canonical_sha256(
                {
                    "effect_id": repair_effect.effect_id,
                    "decision_sha256": decision.decision_sha256,
                    "repair_directive_id": directive.repair_directive_id,
                }
            ),
            issued_at_ms=issued_at,
            not_before_ms=issued_at,
            expires_at_ms=issued_at + 60_000,
            gateway_epoch=self._epoch,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            effect_id=repair_effect.effect_id,
            channel=envelope.channel,
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            principal_scope_hash=envelope.principal_scope_hash,
            capability_manifest_hash=manifest.sha256,
            policy_snapshot_hash=decision.policy_snapshot_sha256,
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            component_manifest_hash=self._components.manifest_sha256,
            life_snapshot_revision=life.snapshot.revision,
            life_snapshot_hash=life.snapshot.sha256,
            risk_class=decision.computed_risk,
            action_id=action.action_id,
            action_version=action.version,
            argument_schema_sha256=action.argument_schema_sha256,
            arguments_hash=arguments_hash,
            workspace_id=workspace_id,
            input_objects=grants,
            object_grants_sha256=canonical_sha256(
                [item.model_dump(mode="json") for item in grants]
            ),
            output_root_id=output_root_id,
            artifact_intent_id=artifact_intent_id,
            max_output_bytes=resources.max_output_bytes,
            max_runtime_ms=resources.max_runtime_ms,
            max_tool_calls=resources.max_tool_calls,
            resource_envelope_sha256=resources.sha256(),
            allowed_side_effects=permission.allowed_side_effects,
            side_effect_envelope_sha256=canonical_sha256(
                {"allowed_side_effects": list(permission.allowed_side_effects)}
            ),
            nonce="execution-nonce-" + canonical_sha256(
                {
                    "effect_id": repair_effect.effect_id,
                    "decision_sha256": decision.decision_sha256,
                    "issued_at_ms": issued_at,
                }
            ),
        )
        ticket = self._authority.execution_signer.sign_execution(payload)
        self._policy_evidence.record_evaluation(
            intent=intent,
            impact=impact,
            permission=permission,
            registry=outer_registry,
            decision=decision,
            ticket=ticket,
            grant=None,
            observed_at_ms=issued_at,
        )
        self._omni_grants.register(
            ticket,
            life_id=life.snapshot.identity_ref,
            life_evidence_ref=life_evidence_ref,
            session_id=envelope.conversation_ref,
            registered_at_ms=issued_at,
            authority_expires_at_ms=issued_at + directive.execution_budget_ms,
        )
        # Final P0-1: repository repairs capture the PRE reality from
        # the independent read-only sensor BEFORE any mutation (never
        # from runtime payloads).
        repo_sensor_pre = None
        if directive.subject_kind == "repository":
            repo_sensor_pre = self._capture_repository_sensor_pre(
                effect_id=repair_effect.effect_id,
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
            )
            if repo_sensor_pre is None:
                # Pre-boundary sensor failure: the runtime was never
                # entered — ONE atomic FAILED_FINAL, no successor.
                self._store.complete_repair_execution(
                    repair_directive_id=directive.repair_directive_id,
                    state="FAILED_FINAL",
                    produced_subject_identity="",
                    produced_subject_kind=directive.subject_kind,
                    runtime_result_ref="repo-pre-sensor-failed",
                    completed_at_ms=time.time_ns() // 1_000_000,
                    error_code="repair.repo_sensor_pre_failed",
                )
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_FAILED",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(repair_effect.effect_id,),
                )
        # Real-time lease fencing: the boundary-crossing time is taken
        # HERE — after policy, ticketing and repository PRE sensing,
        # immediately before the CAS — never the stale issued_at. A
        # lease that expired in the wall-clock world cannot cross on an
        # old timestamp.
        start_at_ms = time.time_ns() // 1_000_000
        try:
            # Final P0-1/P0-2: ONE atomic, CLAIM-FENCED transition crosses
            # BOTH authorities (EffectLedger CLAIMED→STARTED + binding
            # RESERVED→STARTED) BEFORE the runtime. The CAS holds on the
            # caller's claim id + fencing revision + lease live AT THE
            # REAL CROSSING TIME — a claim that lost a takeover OR let
            # its lease expire can never reach the runtime.
            try:
                start_outcome = self._store.start_repair_execution(
                    repair_directive_id=directive.repair_directive_id,
                    effect_id=repair_effect.effect_id,
                    started_at_ms=start_at_ms,
                    dispatch_claim_id=str(
                        reserved["binding"]["dispatch_claim_id"]
                    ),
                    expected_claim_revision=int(
                        reserved["binding"]["claim_revision"]
                    ),
                )
            except StoreCasConflict:
                # This worker's lease expired before the real crossing
                # time (no takeover yet): hand the attempt over without
                # executing anything — a future claim may take over.
                return RepairDispatchResult(
                    execution_outcome="ALREADY_CLAIMED",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(),
                )
            if start_outcome["outcome"] != "STARTED":
                # The boundary was already crossed (by this worker
                # before a crash, or by a takeover winner): the runtime
                # must NEVER run again — this is RECONCILE territory.
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_AMBIGUOUS",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(repair_effect.effect_id,),
                )

            def _execute_repair() -> Any:
                return BackendClient(
                    transport,
                    self._store,
                    ticket_consumer_instance_id=(
                        "compat-frozen-inprocess-" + self._instance_id
                    ),
                ).execute(
                    ticket,
                    repair_arguments,
                    capability_manifest=manifest,
                    trust_bundle=self._authority.execution_trust_bundle(
                        gateway_epoch=self._epoch,
                        now_ms=issued_at,
                    ),
                    now_ms=issued_at,
                    expected_gateway_epoch=self._epoch,
                    minimum_generation=generation.generation,
                )

            try:
                response = _EXECUTION_WATCHDOG_POOL.submit(
                    _execute_repair
                ).result(
                    timeout=max(1.0, directive.execution_budget_ms / 1000.0)
                )
            except concurrent.futures.TimeoutError:
                self._store.complete_repair_execution(
                    repair_directive_id=directive.repair_directive_id,
                    state="AMBIGUOUS",
                    produced_subject_identity="",
                    produced_subject_kind=directive.subject_kind,
                    runtime_result_ref="timeout",
                    completed_at_ms=time.time_ns() // 1_000_000,
                    error_code="repair.execution_timeout",
                )
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_AMBIGUOUS",
                    produced_subject_identity=directive.effective_subject_identity,
                    execution_effect_ids=(repair_effect.effect_id,),
                )
        except BackendClientError as exc:
            self._store.complete_repair_execution(
                repair_directive_id=directive.repair_directive_id,
                state=("AMBIGUOUS" if exc.ambiguous else "FAILED_FINAL"),
                produced_subject_identity="",
                produced_subject_kind=directive.subject_kind,
                runtime_result_ref=exc.code or "",
                completed_at_ms=time.time_ns() // 1_000_000,
                error_code=exc.code,
            )
            return RepairDispatchResult(
                execution_outcome=(
                    "EXECUTION_AMBIGUOUS" if exc.ambiguous else "EXECUTION_FAILED"
                ),
                produced_subject_identity=directive.effective_subject_identity,
                execution_effect_ids=(repair_effect.effect_id,),
            )
        finally:
            self._omni_grants.unregister(ticket.payload.ticket_id)

        response_status = str(
            getattr(getattr(response, "result", None), "status", "FAILED_FINAL")
        )
        if response_status != "SUCCEEDED":
            terminal = (
                "AMBIGUOUS" if response_status == "AMBIGUOUS" else "FAILED_FINAL"
            )
            self._store.complete_repair_execution(
                repair_directive_id=directive.repair_directive_id,
                state=terminal,
                produced_subject_identity="",
                produced_subject_kind=directive.subject_kind,
                runtime_result_ref=(
                    getattr(getattr(response, "result", None), "error_code", "")
                    or ""
                ),
                completed_at_ms=time.time_ns() // 1_000_000,
                error_code=(
                    getattr(getattr(response, "result", None), "error_code", None)
                    or "repair.execution.failed"
                ),
            )
            return RepairDispatchResult(
                execution_outcome=(
                    "EXECUTION_AMBIGUOUS"
                    if response_status == "AMBIGUOUS"
                    else "EXECUTION_FAILED"
                ),
                produced_subject_identity=directive.effective_subject_identity,
                execution_effect_ids=(repair_effect.effect_id,),
            )
        result_payload = (
            response.result_payload
            if isinstance(getattr(response, "result_payload", None), dict)
            else {}
        )
        produced_count = self._register_repair_artifacts(
            response=response,
            result_payload=result_payload,
            directive=directive,
            activation=activation,
            observed_at_ms=time.time_ns() // 1_000_000,
            artifact_intent_id=artifact_intent_id,
            run_sequence=run_sequence,
            workspace_id=workspace_id,
            envelope=envelope,
            artifact_manifests=artifact_manifests,
        )
        # P0-2: the successor is the NEW authoritative reality object
        # per subject kind — artifact → new revision, effect → the
        # re-execution effect itself, repository → the mutation effect
        # with its PRE/POST observation window. The window is bound
        # BEFORE the binding is marked SUCCEEDED, so a crash in between
        # leaves an AMBIGUOUS (reconcilable) binding, never a fake
        # success; a successful binding carries the produced subject
        # and the runtime result reference for recovery.
        produced_subject = directive.effective_subject_identity
        repository_window_ok = True
        if directive.subject_kind == "artifact":
            if produced_count:
                produced_subject = (
                    artifact_manifests[-1].artifact_revision_id
                )
        elif directive.subject_kind == "effect":
            produced_subject = repair_effect.effect_id
        elif directive.subject_kind == "repository":
            repository_window_ok = self._bind_repository_sensor_post(
                sensor_pre=repo_sensor_pre,
                subject_effect_id=repair_effect.effect_id,
                directive=directive,
            )
            if repository_window_ok:
                produced_subject = repair_effect.effect_id
        if not repository_window_ok:
            # The mutation may have happened but its window is unproven
            # — that is AMBIGUOUS, never a silent failure. Both
            # authorities move atomically.
            self._store.complete_repair_execution(
                repair_directive_id=directive.repair_directive_id,
                state="AMBIGUOUS",
                produced_subject_identity="",
                produced_subject_kind=directive.subject_kind,
                runtime_result_ref="repo-window-invalid",
                completed_at_ms=time.time_ns() // 1_000_000,
                error_code="repair.repo_window_invalid",
            )
            return RepairDispatchResult(
                execution_outcome="EXECUTION_AMBIGUOUS",
                produced_subject_identity=directive.effective_subject_identity,
                execution_effect_ids=(repair_effect.effect_id,),
            )
        # Persist the produced reality FIRST, then terminalize the
        # binding — recovery reads the binding without re-running the
        # runtime.
        # ONE atomic transition: binding + EffectLedger both SUCCEEDED,
        # with the produced subject and runtime result persisted.
        self._store.complete_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            state="SUCCEEDED",
            produced_subject_identity=produced_subject,
            produced_subject_kind=directive.subject_kind,
            runtime_result_ref=(
                getattr(response, "response_sha256", None) or "runtime-result"
            ),
            runtime_result_sha256=(
                getattr(response, "response_sha256", "") or ""
            ),
            completed_at_ms=time.time_ns() // 1_000_000,
        )
        return RepairDispatchResult(
            execution_outcome="DISPATCHED",
            produced_subject_identity=produced_subject,
            execution_effect_ids=(repair_effect.effect_id,),
        )

    def _capture_repository_sensor_pre(
        self, *, effect_id: str, request_id: str, run_id: str,
        generation: int,
    ):
        """Final P0-2: the repository PRE reality comes from the
        INDEPENDENT read-only sensor over the workspace Git authority —
        never from a runtime payload. Returns (identity, observation)
        or None when sensing fails (the repair then fails closed)."""
        try:
            from v3.repository_perception import LocalGitRepositoryProvider

            provider = LocalGitRepositoryProvider()
            identity = provider.discover(str(self._workspace_root))
            if identity is None:
                return None
            pre = provider.observe(identity)
            self._store.put_repository_observation(
                observation_sha256=pre.observation_sha256,
                observation_payload=pre.model_dump(mode="json"),
                request_id=request_id,
                run_id=run_id,
                generation=generation,
                effect_id=effect_id,
                repository_id=pre.identity.repository_id,
                head_commit=pre.revision.head_commit,
                observed_at_ms=pre.observed_at_ms,
                recorded_at_ms=time.time_ns() // 1_000_000,
            )
            return (identity, pre)
        except Exception:
            return None

    def _bind_repository_sensor_post(
        self, *, sensor_pre, subject_effect_id: str, directive,
    ) -> bool:
        """Final P0-2: the POST window is likewise captured by the
        independent sensor (delta from the PRE revision) after the
        runtime mutation, then both observations are bound to the NEW
        subject effect. A sensing failure is NEVER a success."""
        try:
            from v3.repository_perception import LocalGitRepositoryProvider

            identity, pre = sensor_pre
            provider = LocalGitRepositoryProvider()
            post = provider.observe_delta(identity, pre.revision)
            self._store.put_repository_observation(
                observation_sha256=post.observation_sha256,
                observation_payload=post.model_dump(mode="json"),
                request_id=directive.request_id,
                run_id=directive.run_id,
                generation=directive.generation,
                effect_id=subject_effect_id,
                repository_id=post.identity.repository_id,
                head_commit=post.revision.head_commit,
                observed_at_ms=post.observed_at_ms,
                recorded_at_ms=time.time_ns() // 1_000_000,
            )
            for role, observation in (("PRE", pre), ("POST", post)):
                self._store.put_repository_observation_binding(
                    observation_sha256=observation.observation_sha256,
                    request_id=directive.request_id,
                    run_id=directive.run_id,
                    generation=directive.generation,
                    subject_effect_id=subject_effect_id,
                    observation_role=role,
                    observed_at_ms=observation.observed_at_ms,
                    recorded_at_ms=observation.observed_at_ms + 2,
                )
            return True
        except Exception:
            return False

    def _register_repair_artifacts(
        self,
        *,
        response,
        result_payload,
        directive,
        activation,
        observed_at_ms,
        artifact_intent_id,
        run_sequence,
        workspace_id,
        envelope,
        artifact_manifests,
    ) -> int:
        """Run repaired artifact descriptors through the SAME
        ArtifactGate/QC pipeline; append passing manifests. Returns the
        number of newly registered manifests."""
        raw_artifacts = result_payload.get("artifacts")
        if not isinstance(raw_artifacts, list):
            return 0
        gate = ArtifactGate(self._objects, self._facts)
        docx_qc = DocxQcService(self._objects, self._facts)
        integrity_qc = ArtifactIntegrityQcService(self._objects, self._facts)
        producer_fact_id = (
            getattr(getattr(response, "result", None), "fact_ids", None)
            or ("fact-repair-" + directive.repair_directive_id[4:20],)
        )[0]
        produced = 0
        for index, item in enumerate(raw_artifacts):
            if not isinstance(item, dict):
                continue
            try:
                accepted = gate.accept(
                    ArtifactCandidate(
                        producer_fact_id=producer_fact_id,
                        object_id=str(item["object_id"]),
                        expected_sha256=str(item["sha256"]),
                        expected_size_bytes=int(item["size_bytes"]),
                        run_sequence=run_sequence,
                        artifact_intent_id=(
                            f"{artifact_intent_id}"
                            f"-r{directive.repair_attempt_no}-{index + 1}"
                        ),
                        revision=1,
                        workspace_id=workspace_id,
                        filename=str(item["filename"]),
                        declared_mime=str(item["mime"]),
                        format_id=str(item["format_id"]),
                        created_at_ms=observed_at_ms,
                    )
                )
                if accepted.manifest.format_id == "docx":
                    docx_minimum_words = 30
                    docx_items_hint = re.search(
                        r"各?(\d+)\s*[条点项]", envelope.text or ""
                    )
                    if docx_items_hint:
                        docx_minimum_words = max(
                            30, int(docx_items_hint.group(1)) * 12
                        )
                    outcome = docx_qc.evaluate(
                        accepted,
                        run_sequence=run_sequence,
                        policy=DocxQcPolicy(
                            minimum_word_count=docx_minimum_words,
                            maximum_word_count=10_000_000,
                        ),
                        checked_at_ms=observed_at_ms,
                    )
                else:
                    outcome = integrity_qc.evaluate(
                        accepted,
                        run_sequence=run_sequence,
                        checked_at_ms=observed_at_ms,
                    )
                if outcome.passed:
                    manifest = outcome.registration.record.manifest
                    artifact_manifests.append(manifest)
                    # P1-6: QC-passed repair artifacts enter the Store's
                    # artifact authority projection.
                    self._store.register_artifact_subject(
                        artifact_revision_id=manifest.artifact_revision_id,
                        object_id=manifest.content_object_id,
                        artifact_sha256=manifest.sha256,
                        request_id=directive.request_id,
                        run_id=directive.run_id,
                        generation=directive.generation,
                        registered_at_ms=observed_at_ms,
                    )
                    produced += 1
            except (
                ArtifactGateError,
                ArtifactIntegrityQcError,
                DocxQcError,
                KeyError,
                TypeError,
                ValueError,
            ):
                continue
        return produced

    def process(self, activation: ActiveRequestActivation) -> None:
        envelope = activation.envelope
        generation = activation.generation
        now_ms = time.time_ns() // 1_000_000
        request_id = activation.entry.request_id
        run_id = generation.run_id
        run_sequence = generation.run_sequence
        execution_entity = "execution-" + run_id
        delivery_entity = "delivery-" + run_id
        self._initialize("execution", execution_entity, activation, now_ms)
        self._initialize("delivery", delivery_entity, activation, now_ms)
        self._advance("request", request_id, "PLANNING", now_ms=now_ms)
        self._advance("execution", execution_entity, "PLANNED", now_ms=now_ms)

        manifest = compatibility_capability_manifest(
            self._components.manifest_sha256,
            generated_at_ms=self._components.generated_at_ms,
        )
        skill_recommendation = (
            None
            if self._skill_authority is None
            else self._skill_authority.system_recommend(
                envelope.text,
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
                decided_at_ms=now_ms,
            )
        )
        history = self._context_projector.project(
            session_scope_hash=activation.entry.session_scope_hash,
            before_sequence=activation.queue.sequence,
            current_request_id=request_id,
        )
        profile = LifeProfileBindings(user_callsign="用户")
        try:
            life = self._acquire_life_snapshot(
                envelope,
                profile,
                activation,
                now_ms,
                current_context_tokens=estimate_projected_context_tokens(
                    history.messages,
                    envelope.text,
                ),
            )
        except Exception as exc:
            # Life is the sole authority for identity, Soul, memory revision and
            # viability.  A synthetic snapshot would create facts that never
            # existed and could let an execution continue outside its identity
            # boundary.  Fail closed and let the orchestration failure path
            # persist the terminal/interruption evidence.
            diagnostic_log(
                "[ORCH-LIFE-FAIL] "
                f"request_id={request_id} run_id={run_id} "
                f"type={type(exc).__name__} code={getattr(exc, 'code', 'unknown')} "
                f"message={str(exc)[:500]}"
            )
            raise OrchestrationError("orchestration.life.snapshot_unavailable") from exc
        attachments = [
            {
                "filename": item.filename,
                "object_id": item.object_id,
                "revision": item.revision,
            }
            for item in envelope.attachments
        ]
        knowledge_references: list[dict[str, Any]] = []
        retriever = self._knowledge_retriever
        if callable(retriever) and envelope.text.strip():
            try:
                retrieval = retriever(envelope.text)
                raw_cards = retrieval.get("cards") if isinstance(retrieval, Mapping) else []
                knowledge_references = [
                    _model_safe_knowledge_reference(item)
                    for item in raw_cards[:6]
                    if isinstance(item, Mapping)
                ] if isinstance(raw_cards, list) else []
            except Exception as exc:
                diagnostic_log(
                    "[ORCH-KNOWLEDGE-RETRIEVAL-FAIL] "
                    f"request_id={request_id} type={type(exc).__name__} message={str(exc)[:300]}"
                )
        arguments: dict[str, object] = {
            "attachments": attachments,
            "channel_message_ref": envelope.channel_message_ref,
            "conversation_ref": envelope.conversation_ref,
            "knowledge_references": knowledge_references,
            "life_snapshot": life.snapshot.model_dump(mode="json"),
            "recent_messages": [dict(item) for item in history.messages],
            "conversation_projection": history.metadata(),
            "skill_recommendation": (
                None
                if skill_recommendation is None
                else skill_recommendation.model_dump(mode="json")
            ),
            "text": envelope.text,
            "user_callsign": profile.user_callsign,
        }
        arguments_hash = canonical_sha256(arguments)
        action = manifest.actions[0]
        effect_intent = canonical_sha256(
            {
                "action_id": action.action_id,
                "action_version": action.version,
                "arguments_hash": arguments_hash,
                "capability_manifest_hash": manifest.sha256,
                "life_snapshot_hash": life.snapshot.sha256,
            }
        )
        effect = derive_effect_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=effect_intent,
        )
        claim = EffectClaim(
            effect_id=effect.effect_id,
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=effect_intent,
            owner_component_id="tiangong-backend",
            claimed_at_ms=now_ms,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        existing_effect, created = self._store.claim_effect(claim)
        if not created and existing_effect.result is not None:
            raise OrchestrationError("orchestration.execution.already_terminal")
        _append_orchestration_effect_event(
            self._store,
            event_key=f"step.prepared:{effect.effect_id}",
            event_type="step.prepared",
            payload={
                "disposition": "prepared",
                "effect_state": existing_effect.state,
                "source": "gateway_orchestration",
            },
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            effect_id=effect.effect_id,
            created_at_ms=now_ms,
        )
        persist_working_checkpoint(
            self._store,
            life_id=life.snapshot.identity_ref,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            user_goal=envelope.text,
            active_plan=(
                "execute the generation-fenced backend effect",
                "validate every declared artifact",
                "apply the channel-independent completion gate",
            ),
            pending_effect_ids=(effect.effect_id,),
            latest_safe_step="request, life snapshot, and generation fence are durably bound",
            next_step="resume or reconcile the fenced backend effect",
            recovery_preconditions=(
                f"generation fence {generation.fence.fence_id} remains authoritative",
            ),
            created_at_ms=now_ms,
        )
        workspace_id = "workspace-" + canonical_sha256(str(self._workspace_root))
        output_root_id = "output-" + canonical_sha256({"request_id": request_id, "run_id": run_id})
        artifact_intent_id = "artifact-" + canonical_sha256({"request_id": request_id, "run_id": run_id})
        grants = tuple(
            sorted(
                (
                    ObjectGrant(
                        object_id=item.object_id,
                        revision=item.revision,
                        sha256=item.sha256,
                        size_bytes=item.size_bytes,
                        mime=item.mime,
                        tenant_id=item.tenant_id,
                        link_account_id=item.link_account_id,
                        conversation_scope_hash=item.conversation_scope_hash,
                    )
                    for item in envelope.attachments
                ),
                key=lambda item: (item.object_id, item.revision),
            )
        )
        issued_at = time.time_ns() // 1_000_000
        outer_registry = compatibility_action_registry(manifest, generated_at_ms=issued_at)
        permission = outer_registry.permissions[0]
        life_evidence_payload = {
            "schema": "tiangong.gateway.life-snapshot-evidence.v1",
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation.generation,
            "life_id": life.snapshot.identity_ref,
            "life_snapshot_id": life.snapshot.snapshot_id,
            "life_snapshot_revision": life.snapshot.revision,
            "life_snapshot_sha256": life.snapshot.sha256,
            "observed_at_ms": issued_at,
        }
        life_evidence_ref = "lev_" + canonical_sha256(life_evidence_payload)
        self._policy_evidence.record("life-event", life_evidence_ref, life_evidence_payload)
        resources = ResourceEnvelope(
            max_runtime_ms=action.max_runtime_ms,
            max_output_bytes=action.max_output_bytes,
            max_tool_calls=action.max_tool_calls,
        )
        # D-08: the authorization provenance of a model run is exactly the
        # current user instruction bound to this request plus the Life evidence
        # snapshot.  Retrieved knowledge and attachments are EXTERNAL_DATA and
        # are deliberately absent from the authorization set.
        authorization_source_refs = tuple(
            sorted(
                (
                    SourceRef(
                        source_type="CURRENT_USER_INSTRUCTION",
                        object_id=request_id,
                        object_revision=1,
                        sha256=canonical_sha256(envelope.text or ""),
                    ),
                    SourceRef(
                        source_type="PREAUTHORIZED_USER_FACT",
                        object_id=life_evidence_ref,
                        object_revision=1,
                        sha256=life_evidence_ref[4:],
                    ),
                ),
                key=lambda ref: ref.sort_key(),
            )
        )
        intent = ActionIntent(
            intent_id="intent-" + canonical_sha256(
                {
                    "effect_id": effect.effect_id,
                    "arguments_sha256": arguments_hash,
                    "life_evidence_ref": life_evidence_ref,
                    "created_at_ms": issued_at,
                }
            ),
            source="chat",
            life_id=life.snapshot.identity_ref,
            principal_scope_hash=envelope.principal_scope_hash,
            conversation_scope_hash=envelope.conversation_scope_hash,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            action_id=action.action_id,
            action_version=action.version,
            arguments_sha256=arguments_hash,
            workspace_id=workspace_id,
            workspace_scope_hash=self._omni_grants.workspace_scope_hash,
            input_object_refs=tuple(sorted(item.object_id for item in grants)),
            requested_side_effects=permission.allowed_side_effects,
            requested_resources=resources,
            source_refs=authorization_source_refs,
            payload_sha256=arguments_hash,
            attachment_set_sha256=canonical_sha256(
                [
                    {"object_id": item.object_id, "revision": item.revision, "sha256": item.sha256}
                    for item in grants
                ]
            ),
            life_snapshot_revision=life.snapshot.revision,
            life_snapshot_sha256=life.snapshot.sha256,
            created_at_ms=issued_at,
            expires_at_ms=issued_at + 60_000,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        # D-09: the model-run intent derives its uncertainty from the amount of
        # untrusted external content (attachments, retrieved knowledge) entering
        # the run.  Argument strings are not scanned here: this outer intent is
        # the bounded model invocation itself, and every inner tool call is
        # re-derived with full argument scanning by the Omni grant authority.
        knobs = derive_impact_knobs(
            action.action_id,
            None,
            scan_args=False,
            external_content_count=len(attachments) + len(knowledge_references),
        )
        impact = compute_action_impact(
            intent,
            permission,
            affected_internal_nodes=("node_tiangong_backend_model_runtime",),
            external_recipient_count=knobs["external_recipient_count"],
            credential_scope_milli=knobs["credential_scope_milli"],
            privacy_scope_milli=knobs["privacy_scope_milli"],
            blast_radius_milli=knobs["blast_radius_milli"],
            irreversibility_milli=knobs["irreversibility_milli"],
            uncertainty_milli=knobs["uncertainty_milli"],
            created_at_ms=issued_at,
        )
        policy_snapshot_sha256 = canonical_sha256(
            {
                "policy": "tiangong.gateway.model-run.autonomous-a0-a4.a5-deny.v3",
                "registry_sha256": outer_registry.registry_sha256,
            }
        )
        decision = PolicyEngine(
            outer_registry,
            policy_snapshot_sha256=policy_snapshot_sha256,
            skill_catalog_hash=self._release_manifest.skill_catalog_sha256,
            capability_manifest_hash=manifest.sha256,
            component_manifest_hash=self._components.manifest_sha256,
        ).evaluate(
            intent,
            impact,
            decided_at_ms=issued_at,
            authorization_source_refs=authorization_source_refs,
        )
        if decision.outcome != "ALLOW":
            self._policy_evidence.record_evaluation(
                intent=intent,
                impact=impact,
                permission=permission,
                registry=outer_registry,
                decision=decision,
                ticket=None,
                grant=None,
                observed_at_ms=issued_at,
            )
            raise OrchestrationError("orchestration.policy.rejected")

        payload = ExecutionTicketPayload(
            ticket_id="execution-ticket-" + canonical_sha256(
                {"effect_id": effect.effect_id, "decision_sha256": decision.decision_sha256}
            ),
            issued_at_ms=issued_at,
            not_before_ms=issued_at,
            expires_at_ms=issued_at + 60_000,
            gateway_epoch=self._epoch,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            effect_id=effect.effect_id,
            channel=envelope.channel,
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            principal_scope_hash=envelope.principal_scope_hash,
            capability_manifest_hash=manifest.sha256,
            policy_snapshot_hash=decision.policy_snapshot_sha256,
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            component_manifest_hash=self._components.manifest_sha256,
            life_snapshot_revision=life.snapshot.revision,
            life_snapshot_hash=life.snapshot.sha256,
            risk_class=decision.computed_risk,
            action_id=action.action_id,
            action_version=action.version,
            argument_schema_sha256=action.argument_schema_sha256,
            arguments_hash=arguments_hash,
            workspace_id=workspace_id,
            input_objects=grants,
            object_grants_sha256=canonical_sha256(
                [item.model_dump(mode="json") for item in grants]
            ),
            output_root_id=output_root_id,
            artifact_intent_id=artifact_intent_id,
            max_output_bytes=resources.max_output_bytes,
            max_runtime_ms=resources.max_runtime_ms,
            max_tool_calls=resources.max_tool_calls,
            resource_envelope_sha256=resources.sha256(),
            allowed_side_effects=permission.allowed_side_effects,
            side_effect_envelope_sha256=canonical_sha256(
                {"allowed_side_effects": list(permission.allowed_side_effects)}
            ),
            nonce="execution-nonce-" + canonical_sha256(
                {
                    "effect_id": effect.effect_id,
                    "decision_sha256": decision.decision_sha256,
                    "issued_at_ms": issued_at,
                }
            ),
        )
        ticket = self._authority.execution_signer.sign_execution(payload)
        self._policy_evidence.record_evaluation(
            intent=intent,
            impact=impact,
            permission=permission,
            registry=outer_registry,
            decision=decision,
            ticket=ticket,
            grant=None,
            observed_at_ms=issued_at,
        )
        self._advance("execution", execution_entity, "TICKET_ISSUED", now_ms=issued_at)
        self._advance("execution", execution_entity, "CLAIMED", now_ms=issued_at)
        self._advance("request", request_id, "EXECUTING", now_ms=issued_at)

        def started(started_at_ms: int) -> None:
            self._store.mark_effect_started(effect.effect_id, started_at_ms=started_at_ms)
            _append_orchestration_effect_event(
                self._store,
                event_key=f"step.dispatched:{effect.effect_id}",
                event_type="step.dispatched",
                payload={
                    "effect_state": "SIDE_EFFECT_STARTED",
                    "dispatch_boundary": "gateway_orchestration",
                },
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
                effect_id=effect.effect_id,
                created_at_ms=started_at_ms,
            )
            self._advance("execution", execution_entity, "RUNNING", now_ms=started_at_ms)

        def context_compacted(context_envelope: dict[str, Any], compacted_at_ms: int) -> None:
            active = self._store.get_active_request_capsule(
                request_id,
                run_id=run_id,
                generation=generation.generation,
            )
            omitted = context_envelope.get("omitted_blocks")
            omitted_count = len(omitted) if isinstance(omitted, list) else 0
            persist_compression_checkpoint(
                self._store,
                life_id=life.snapshot.identity_ref,
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
                user_goal=envelope.text,
                hard_constraints=() if active is None else active.capsule.hard_constraints,
                active_plan=() if active is None else active.capsule.active_plan,
                verified_fact_ids=() if active is None else active.capsule.verified_fact_ids,
                artifact_refs=() if active is None else active.capsule.artifact_refs,
                pending_effect_ids=(effect.effect_id,),
                latest_safe_step=(
                    f"life context compiler retained the bounded continuity set and omitted "
                    f"{omitted_count} low-weight blocks"
                ),
                next_step="continue the fenced effect from the verified compressed context",
                recovery_preconditions=(
                    "reload the latest compression capsule instead of raw tool transcripts",
                ),
                created_at_ms=compacted_at_ms,
            )

        transport = FrozenBackendCompatibilityTransport(
            self._objects,
            backend_token=self._backend_token,
            life_token=self._life_token,
            workspace_root=self._workspace_root,
            gateway_url=self._gateway_url,
            backend_client=getattr(self, "_backend_compat_client", None),
            life_client=getattr(self, "_life_compat_client", None),
            on_backend_start=started,
            on_context_compaction=context_compacted,
        )
        self._omni_grants.register(
            ticket,
            life_id=life.snapshot.identity_ref,
            life_evidence_ref=life_evidence_ref,
            session_id=envelope.conversation_ref,
            registered_at_ms=issued_at,
            authority_expires_at_ms=issued_at + action.max_runtime_ms,
        )
        def _execute_compat() -> Any:
            return BackendClient(
                transport,
                self._store,
                ticket_consumer_instance_id="compat-frozen-inprocess-" + self._instance_id,
            ).execute(
                ticket,
                arguments,
                capability_manifest=manifest,
                trust_bundle=self._authority.execution_trust_bundle(
                    gateway_epoch=self._epoch,
                    now_ms=issued_at,
                ),
                now_ms=issued_at,
                expected_gateway_epoch=self._epoch,
                minimum_generation=generation.generation,
            )

        # 执行预算按 3 倍放宽：默认效果截止 12 分钟（720s），单次动作最多 60 分钟
        # （与执行契约 max_runtime_ms 上限一致），支撑超复杂度/混合超长任务。
        watchdog_ms = 720_000
        try:
            watchdog_ms = max(
                60_000,
                min(int(action.max_runtime_ms or 0) or 720_000, 3_600_000),
            )
        except Exception:
            watchdog_ms = 720_000
        try:
            # The frozen backend executes in the gateway's watchdog pool.  The
            # v3 simple chain sets per-request ContextVars (e.g.
            # learning_intent_verified) on the caller's context; without an
            # explicit copy they are lost at the pool boundary and
            # learning.ingest / life-bound actions fail in the packaged build.
            from contracts.reliability import (
                reset_execution_deadline,
                set_execution_deadline_ms,
            )

            deadline_at_ms = int(time.time_ns() // 1_000_000) + watchdog_ms
            # Deadline travels ONLY through the ContextVar: every backend
            # thread boundary now copies the context (LLM hard-timeout runners,
            # the parallel tool executor, and this watchdog pool all run
            # contextvars.copy_context()).  The former process-env channel
            # poisoned concurrent background model calls (life heartbeat,
            # autonomous activities, cognition tasks) with the chat effect's
            # remaining time — in a single-process desktop build those chains
            # share this process.

            def _execute_compat_with_deadline() -> Any:
                token = set_execution_deadline_ms(deadline_at_ms)
                try:
                    return _execute_compat()
                finally:
                    reset_execution_deadline(token)

            execution_future = _EXECUTION_WATCHDOG_POOL.submit(
                contextvars.copy_context().run,
                _execute_compat_with_deadline,
            )
            try:
                response = execution_future.result(timeout=watchdog_ms / 1000.0)
            except concurrent.futures.TimeoutError:
                effect_record = self._store.get_effect(effect.effect_id)
                raise BackendClientError(
                    "effect_execution_timeout",
                    ambiguous=(
                        effect_record is None
                        or effect_record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}
                    ),
                ) from None
        except BackendClientError as exc:
            effect_record = self._store.get_effect(effect.effect_id)
            status = "AMBIGUOUS" if exc.ambiguous or (effect_record and effect_record.state == "SIDE_EFFECT_STARTED") else "FAILED_FINAL"
            result = EffectResult(
                result_id="effect-result-" + effect.effect_id[4:20],
                effect_id=effect.effect_id,
                status=status,
                fact_id="fact-effect-" + effect.effect_id[4:20],
                evidence_sha256=canonical_sha256({"code": exc.code, "status": status}),
                error_code=exc.code,
                observed_at_ms=time.time_ns() // 1_000_000,
                result_sha256="0" * 64,
            ).with_computed_sha256()
            self._store.complete_effect(result)
            terminal = "AMBIGUOUS" if status == "AMBIGUOUS" else "FAILED_FINAL"
            _append_orchestration_effect_event(
                self._store,
                event_key=f"step.{"ambiguous" if terminal == "AMBIGUOUS" else "failed"}:{effect.effect_id}",
                event_type=f"step.{"ambiguous" if terminal == "AMBIGUOUS" else "failed"}",
                payload={"effect_state": terminal, "source": "gateway_orchestration"},
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
                effect_id=effect.effect_id,
                created_at_ms=result.observed_at_ms,
            )
            self._advance(
                "execution",
                execution_entity,
                terminal,
                now_ms=result.observed_at_ms,
                fact_id=result.fact_id,
                evidence_sha256=result.evidence_sha256,
            )
            if status == "AMBIGUOUS":
                self._advance(
                    "execution",
                    execution_entity,
                    "RECONCILE_REQUIRED",
                    now_ms=result.observed_at_ms,
                    fact_id=result.fact_id,
                    evidence_sha256=result.evidence_sha256,
                )
            self._advance(
                "request",
                request_id,
                "FAILED",
                now_ms=result.observed_at_ms,
                fact_id=result.fact_id,
                evidence_sha256=result.evidence_sha256,
            )
            self._persist_interruption(
                activation,
                reason_code=exc.code,
                observed_at_ms=result.observed_at_ms,
                fact_id=result.fact_id,
            )
            self._store.complete_session_request(
                activation.entry.session_scope_hash,
                request_id,
                completed_at_ms=result.observed_at_ms,
            )
            raise OrchestrationError(exc.code, ambiguous=status == "AMBIGUOUS") from exc
        finally:
            self._omni_grants.unregister(ticket.payload.ticket_id)

        observed_at = max(time.time_ns() // 1_000_000, response.result.finished_at_ms)
        facts = self._facts.record_execution(response, observed_at_ms=observed_at)
        effect_status = (
            "SUCCEEDED"
            if response.result.status == "SUCCEEDED"
            else "AMBIGUOUS"
            if response.result.status == "AMBIGUOUS"
            else "FAILED_FINAL"
        )
        effect_result = EffectResult(
            result_id="effect-result-" + response.result.result_id[:120],
            effect_id=effect.effect_id,
            status=effect_status,
            fact_id=response.result.fact_ids[0],
            result_object_id=facts.record.result_payload_object_id,
            result_object_sha256=facts.record.result_payload_sha256,
            evidence_sha256=response.response_sha256,
            error_code=None if effect_status == "SUCCEEDED" else (response.result.error_code or "execution.failed"),
            observed_at_ms=observed_at,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        self._store.complete_effect(effect_result)
        _append_orchestration_effect_event(
            self._store,
            event_key=f"step.{"committed" if effect_status == "SUCCEEDED" else "ambiguous" if effect_status == "AMBIGUOUS" else "failed"}:{effect.effect_id}",
            event_type=f"step.{"committed" if effect_status == "SUCCEEDED" else "ambiguous" if effect_status == "AMBIGUOUS" else "failed"}",
            payload={"effect_state": effect_status, "source": "gateway_orchestration"},
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            effect_id=effect.effect_id,
            created_at_ms=observed_at,
        )
        self._advance(
            "execution",
            execution_entity,
            response.result.status if response.result.status in {"SUCCEEDED", "AMBIGUOUS", "FAILED_FINAL"} else "FAILED_FINAL",
            now_ms=observed_at,
            fact_id=response.result.fact_ids[0],
            evidence_sha256=response.response_sha256,
        )
        if response.result.status != "SUCCEEDED":
            if response.result.status == "AMBIGUOUS":
                self._advance(
                    "execution",
                    execution_entity,
                    "RECONCILE_REQUIRED",
                    now_ms=observed_at,
                    fact_id=response.result.fact_ids[0],
                    evidence_sha256=response.response_sha256,
                )
            self._advance(
                "request",
                request_id,
                "FAILED",
                now_ms=observed_at,
                fact_id=response.result.fact_ids[0],
                evidence_sha256=response.response_sha256,
            )
            self._persist_interruption(
                activation,
                reason_code=(
                    response.result.error_code or "orchestration.execution.failed"
                ),
                observed_at_ms=observed_at,
                fact_id=response.result.fact_ids[0],
            )
            self._store.complete_session_request(
                activation.entry.session_scope_hash,
                request_id,
                completed_at_ms=observed_at,
            )
            raise OrchestrationError(response.result.error_code or "orchestration.execution.failed")

        result_payload = response.result_payload if isinstance(response.result_payload, dict) else {}
        reply = str(result_payload.get("reply_text") or "").strip()
        artifacts = []
        raw_artifacts = result_payload.get("artifacts") if isinstance(result_payload.get("artifacts"), list) else []
        if not reply and raw_artifacts:
            # artifact-only delivery: the model produced verified outputs but
            # no natural-language closeout.  Synthesize a deterministic note
            # from the artifact descriptors instead of failing the request.
            names = [
                str(item.get("filename") or "").strip()
                for item in raw_artifacts
                if isinstance(item, dict) and str(item.get("filename") or "").strip()
            ]
            listed = "、".join(names[:5]) + ("等" if len(names) > 5 else "")
            reply = f"已完成，生成{len(names)}个交付文件：{listed}。" if names else "已完成指定交付。"
        if not reply:
            raise OrchestrationError("orchestration.reply.empty")
        if raw_artifacts:
            self._advance("request", request_id, "VALIDATING_ARTIFACTS", now_ms=observed_at)
        gate = ArtifactGate(self._objects, self._facts)
        docx_qc = DocxQcService(self._objects, self._facts)
        integrity_qc = ArtifactIntegrityQcService(self._objects, self._facts)
        artifact_failures: list[str] = []
        for index, item in enumerate(raw_artifacts):
            if not isinstance(item, dict):
                code = "orchestration.artifact.invalid_descriptor"
                evidence = canonical_sha256({"code": code, "index": index})
                entity = "artifact-rejected-" + canonical_sha256(
                    {"request_id": request_id, "run_id": run_id, "index": index}
                )
                self._initialize("artifact", entity, activation, observed_at)
                self._advance(
                    "artifact",
                    entity,
                    "REJECTED",
                    now_ms=observed_at,
                    fact_id="fact-artifact-" + evidence[:32],
                    evidence_sha256=evidence,
                )
                artifact_failures.append(code)
                continue
            try:
                accepted = gate.accept(
                    ArtifactCandidate(
                        producer_fact_id=response.result.fact_ids[0],
                        object_id=str(item["object_id"]),
                        expected_sha256=str(item["sha256"]),
                        expected_size_bytes=int(item["size_bytes"]),
                        run_sequence=run_sequence,
                        artifact_intent_id=f"{artifact_intent_id}-{index + 1}",
                        revision=1,
                        workspace_id=workspace_id,
                        filename=str(item["filename"]),
                        declared_mime=str(item["mime"]),
                        format_id=str(item["format_id"]),
                        created_at_ms=observed_at,
                    )
                )
                if accepted.manifest.format_id == "docx":
                    # 内容兜底：docx 质检的最小字数按请求推导。固定 1 词的
                    # 下限曾放过"只有标题的空壳文档"（真机 2026-08-29 复现）。
                    docx_minimum_words = 30
                    docx_items_hint = re.search(r"各?(\d+)\s*[条点项]", envelope.text or "")
                    if docx_items_hint:
                        docx_minimum_words = max(30, int(docx_items_hint.group(1)) * 12)
                    outcome = docx_qc.evaluate(
                        accepted,
                        run_sequence=run_sequence,
                        policy=DocxQcPolicy(
                            minimum_word_count=docx_minimum_words,
                            maximum_word_count=10_000_000,
                        ),
                        checked_at_ms=observed_at,
                    )
                else:
                    outcome = integrity_qc.evaluate(
                        accepted,
                        run_sequence=run_sequence,
                        checked_at_ms=observed_at,
                    )
                artifact_entity = outcome.registration.record.manifest.artifact_revision_id
                self._initialize("artifact", artifact_entity, activation, observed_at)
                self._advance(
                    "artifact",
                    artifact_entity,
                    "CREATED",
                    now_ms=observed_at,
                    evidence_sha256=accepted.evidence.evidence_sha256,
                )
                self._advance(
                    "artifact",
                    artifact_entity,
                    "QC_PENDING",
                    now_ms=observed_at,
                )
                qc_record = outcome.registration.record
                if outcome.passed:
                    self._advance(
                        "artifact",
                        artifact_entity,
                        "QC_PASSED",
                        now_ms=observed_at,
                        fact_id=qc_record.fact.fact_id,
                        evidence_sha256=qc_record.result.qc_result_sha256,
                    )
                    artifacts.append(qc_record.manifest)
                    # P1-6: register the QC-passed manifest in the Store's
                    # artifact authority projection (successor boundary).
                    self._store.register_artifact_subject(
                        artifact_revision_id=(
                            qc_record.manifest.artifact_revision_id
                        ),
                        object_id=qc_record.manifest.content_object_id,
                        artifact_sha256=qc_record.manifest.sha256,
                        request_id=request_id,
                        run_id=run_id,
                        generation=generation.generation,
                        registered_at_ms=observed_at,
                    )
                else:
                    self._advance(
                        "artifact",
                        artifact_entity,
                        "QC_FAILED",
                        now_ms=observed_at,
                        fact_id=qc_record.fact.fact_id,
                        evidence_sha256=qc_record.result.qc_result_sha256,
                    )
                    artifact_failures.append("orchestration.artifact.qc_failed")
            except (ArtifactGateError, ArtifactIntegrityQcError, DocxQcError, KeyError, TypeError, ValueError) as exc:
                code = getattr(exc, "code", None) or "orchestration.artifact.validation_failed"
                evidence = canonical_sha256({"code": code, "index": index})
                entity = "artifact-rejected-" + canonical_sha256(
                    {"request_id": request_id, "run_id": run_id, "index": index}
                )
                self._initialize("artifact", entity, activation, observed_at)
                self._advance(
                    "artifact",
                    entity,
                    "REJECTED",
                    now_ms=observed_at,
                    fact_id="fact-artifact-" + evidence[:32],
                    evidence_sha256=evidence,
                )
                artifact_failures.append(str(code))
                continue

        if artifact_failures:
            evidence = canonical_sha256(
                {"artifact_failures": sorted(artifact_failures), "request_id": request_id}
            )
            failed_at = time.time_ns() // 1_000_000
            self._advance(
                "request",
                request_id,
                "FAILED",
                now_ms=failed_at,
                fact_id="fact-artifact-" + evidence[:32],
                evidence_sha256=evidence,
            )
            self._persist_interruption(
                activation,
                reason_code="orchestration.artifact.required_validation_failed",
                observed_at_ms=failed_at,
                fact_id="fact-artifact-" + evidence[:32],
            )
            self._store.complete_session_request(
                activation.entry.session_scope_hash,
                request_id,
                completed_at_ms=failed_at,
            )
            raise OrchestrationError("orchestration.artifact.required_validation_failed")

        if envelope.channel == "desktop":
            # The desktop renderer pulls this already-persisted result back
            # through the authenticated 7184 status route.  It must never be
            # handed to 7176 or interpreted as a WeChat/Feishu delivery.
            # M4.1 Final §10: production verification wiring — if an
            # active plan exists, the Executor MUST run (records +
            # readiness are persisted before the gate reads them).
            active_plan = self._store.get_active_verification_plan(
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
            )
            verification_disposition = None
            verification_failure_evidence = None
            if active_plan is not None:
                from total_gateway.verification_plan_executor import (
                    VerificationPlanExecutor,
                )
                executor = VerificationPlanExecutor(
                    snapshot=_verification_snapshot(
                        self._store, active_plan.registry_snapshot_sha256
                    ),
                    store=self._store,
                    object_store=self._objects,
                    fact_ledger=self._facts,
                    plan=active_plan,
                )
                readiness = executor.execute(
                    evaluated_at_ms=time.time_ns() // 1_000_000,
                    artifact_manifests=tuple(artifacts),
                )
                # M5 Final #2: FAIL → the FULL repair loop — directive →
                # EXISTING runtime dispatch → successor → SAME-predicate
                # re-verification. Never stop at the disposition.
                if not readiness.verification_ready:
                    from total_gateway.verification_repair_coordinator import (
                        RepairDispatchResult,
                        VerificationRepairCoordinator,
                    )
                    coordinator = VerificationRepairCoordinator(
                        store=self._store,
                    )

                    def _repair_dispatch(directive):
                        # Bridge to the EXISTING runtime: the same
                        # PolicyEngine → ExecutionTicket → BackendClient →
                        # ArtifactGate/QC authorities as the primary
                        # execution. No second runtime.
                        return self._dispatch_repair_directive(
                            directive=directive,
                            activation=activation,
                            envelope=envelope,
                            manifest=manifest,
                            action=action,
                            permission=permission,
                            outer_registry=outer_registry,
                            transport=transport,
                            arguments=arguments,
                            grants=grants,
                            resources=resources,
                            life=life,
                            life_evidence_ref=life_evidence_ref,
                            workspace_id=workspace_id,
                            output_root_id=output_root_id,
                            artifact_intent_id=artifact_intent_id,
                            request_id=request_id,
                            run_id=run_id,
                            run_sequence=run_sequence,
                            generation=generation,
                            artifact_manifests=artifacts,
                        )

                    def _repair_reverify():
                        reverify_executor = VerificationPlanExecutor(
                            snapshot=_verification_snapshot(
                                self._store,
                                active_plan.registry_snapshot_sha256,
                            ),
                            store=self._store,
                            object_store=self._objects,
                            fact_ledger=self._facts,
                            plan=active_plan,
                        )
                        return reverify_executor.execute(
                            evaluated_at_ms=time.time_ns() // 1_000_000,
                            artifact_manifests=tuple(artifacts),
                        )

                    readiness, _ = coordinator.execute_repair_loop(
                        plan=active_plan,
                        readiness=readiness,
                        dispatch=_repair_dispatch,
                        reverify=_repair_reverify,
                    )
                    # M5 Final #7 + P1-9: read CURRENT disposition and its
                    # FailureEvidence from Store (authoritative source);
                    # the Gate validates the full binding.
                    verification_disposition = self._store.get_current_verification_disposition(
                        request_id=request_id,
                        run_id=run_id,
                        generation=generation.generation,
                        verification_plan_id=active_plan.verification_plan_id,
                        readiness_sha256=readiness.readiness_sha256,
                    )
                    if verification_disposition is not None:
                        verification_failure_evidence = self._store.get_verification_failure_evidence_by_id(
                            verification_disposition.failure_evidence_id
                        )
            decision = evaluate_desktop_completion(
                objects=self._objects,
                facts=self._facts,
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
                execution_effect_id=effect.effect_id,
                candidate_text=reply,
                artifacts=tuple(artifacts),
                head_state_reader=self._store.get_effect_head_state,
                verification_readiness=self._store.get_latest_verification_readiness(
                    request_id=request_id,
                    run_id=run_id,
                    generation=generation.generation,
                ),
                active_plan=active_plan,
                verification_disposition=verification_disposition,
                verification_failure_evidence=verification_failure_evidence,
                disposition_authority_reader=(
                    self._store.get_verification_disposition_by_id
                ),
            )
            desktop_now = time.time_ns() // 1_000_000
            desktop_evidence = canonical_sha256(
                {
                    "artifact_manifests": [item.manifest_sha256 for item in artifacts],
                    "completion_decision_sha256": decision.decision_sha256,
                    "domain": "tiangong.gateway.desktop-result-available.v1",
                    "request_id": request_id,
                    "response_sha256": response.response_sha256,
                    "run_id": run_id,
                }
            )
            desktop_fact_id = "fact-desktop-result-" + desktop_evidence[:32]
            # Life is the sole authority for the completed interaction.  Commit
            # before crossing the desktop delivery boundary so a failed Life
            # write cannot leave a CHANNEL_ACCEPTED result that is absent from
            # the authoritative life journal.  The commit is idempotent by
            # request_id, so retry/recovery remains safe.
            self._commit_life_execution(
                request_id=request_id,
                run_id=run_id,
                generation=generation.generation,
                life_id=life.snapshot.identity_ref,
                session_scope_hash=activation.entry.session_scope_hash,
                principal_scope_hash=envelope.principal_scope_hash,
                workspace_id=workspace_id,
                user_goal=envelope.text,
                final_result=reply,
                fact_ids=(desktop_fact_id, *tuple(response.result.fact_ids)),
                completed_at_ms=desktop_now,
            )
            self._advance("delivery", delivery_entity, "PLANNED", now_ms=desktop_now)
            self._advance("delivery", delivery_entity, "TICKET_ISSUED", now_ms=desktop_now)
            self._advance("delivery", delivery_entity, "SENDING", now_ms=desktop_now)
            self._advance(
                "delivery",
                delivery_entity,
                "CHANNEL_ACCEPTED",
                now_ms=desktop_now,
                fact_id=desktop_fact_id,
                evidence_sha256=desktop_evidence,
            )
            self._advance("request", request_id, "DELIVERING", now_ms=desktop_now)
            persist_terminal_completion(
                self._store,
                decision,
                life_id=life.snapshot.identity_ref,
                user_goal=envelope.text,
                final_result=reply,
                created_at_ms=desktop_now,
                verified_fact_ids=(desktop_fact_id,),
                artifact_refs=tuple(
                    sorted(item.artifact_revision_id for item in artifacts)
                ),
            )
            self._advance(
                "request",
                request_id,
                "COMPLETED",
                now_ms=desktop_now,
                fact_id=desktop_fact_id,
                evidence_sha256=desktop_evidence,
            )
            self._store.complete_session_request(
                activation.entry.session_scope_hash,
                request_id,
                completed_at_ms=desktop_now,
            )
            return

        delivery_now = time.time_ns() // 1_000_000
        try:
            self._activator.heartbeat(activation, now_ms=delivery_now)
        except Exception:
            # 交付边界租约过期（系统睡眠/心跳失联）：执行已经完成，
            # 绝不能因租约过期而废弃已产出的结果——先按 ID 重新接管
            #（generation+1 recovery lease）再续约，接管失败才上抛。
            recovered_activation = None
            try:
                recovered_activation = self._activator.recover(
                    activation.entry.request_id,
                    now_ms=time.time_ns() // 1_000_000,
                )
            except Exception:
                recovered_activation = None
            if recovered_activation is None:
                raise
            activation = recovered_activation
        scope = OutboundScope(
            channel=envelope.channel,
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_ref=envelope.conversation_ref,
            recipient_ref=envelope.sender_ref,
            reply_to_message_ref=envelope.channel_message_ref,
        )
        scope_keys = derive_outbound_scope_keys(scope)
        channel_policy_hash = (
            _WECHAT_POLICY_SHA256 if envelope.channel == "wechat" else _FEISHU_POLICY_SHA256
        )
        # The frozen iLink protocol transports text and media as distinct
        # effects.  Until the public contract supports a multi-delivery
        # completion group, an artifact-producing WeChat request sends the
        # verified artifact only; ordinary WeChat requests remain text-only.
        # Feishu can bind mixed parts in one platform-deduplicated delivery.
        include_text = not artifacts or envelope.channel == "feishu"
        preliminary_parts = (
            ([{"index": 0, "kind": "text", "sha256": text_sha256(reply)}] if include_text else [])
            + [
                {
                    "index": index + (1 if include_text else 0),
                    "kind": "artifact",
                    "sha256": item.manifest_sha256,
                }
                for index, item in enumerate(artifacts)
            ]
        )
        payload_manifest_sha256 = canonical_sha256(preliminary_parts)
        delivery_identity = derive_delivery_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation.generation,
            recipient_scope_hash=scope_keys.recipient_scope_hash,
            reply_to_message_ref=envelope.channel_message_ref,
            payload_manifest_sha256=payload_manifest_sha256,
        )
        delivery_intent = canonical_sha256(
            {
                "channel": envelope.channel,
                "delivery_id": delivery_identity.delivery_id,
                "payload_manifest_sha256": payload_manifest_sha256,
                "recipient_scope_hash": scope_keys.recipient_scope_hash,
            }
        )
        delivery_effect = derive_effect_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation.generation,
            effect_kind="delivery",
            ordinal=0,
            intent_sha256=delivery_intent,
        )
        parts = []
        if include_text:
            parts.append(
                OutboundPart(
                    part_id="part-text-" + delivery_effect.effect_id[4:20],
                    index=0,
                    kind="text",
                    text=reply,
                    text_sha256=text_sha256(reply),
                )
            )
        parts.extend(
            OutboundPart(
                part_id=f"part-artifact-{index + 1}-{delivery_effect.effect_id[4:12]}",
                index=index + (1 if include_text else 0),
                kind="artifact",
                artifact=artifact,
            )
            for index, artifact in enumerate(artifacts)
        )
        plan = OutboundPlan(
            outbound_plan_id="outbound-plan-" + canonical_sha256(
                {"delivery_id": delivery_identity.delivery_id, "effect_id": delivery_effect.effect_id}
            ),
            delivery_id=delivery_identity.delivery_id,
            effect_id=delivery_effect.effect_id,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            channel=envelope.channel,
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_ref=envelope.conversation_ref,
            conversation_scope_hash=scope_keys.conversation_scope_hash,
            recipient_scope_hash=scope_keys.recipient_scope_hash,
            reply_to_message_ref=envelope.channel_message_ref,
            channel_policy_hash=channel_policy_hash,
            created_at_ms=delivery_now,
            parts=tuple(parts),
            plan_sha256="0" * 64,
        ).with_computed_plan_sha256()
        delivery_assembly = build_delivery_outbox_payload(
            plan,
            life_id=life.snapshot.identity_ref,
            session_scope_hash=activation.entry.session_scope_hash,
            execution_effect_id=effect.effect_id,
        )
        payload_bytes = canonical_json_bytes(delivery_assembly.model_dump(mode="json"))
        payload_object = self._objects.put_bytes(
            payload_bytes,
            kind="payload",
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            created_at_ms=delivery_now,
        ).reference
        outbox = OutboxIntent(
            outbox_id=derive_outbox_id(
                delivery_effect.effect_id,
                "tiangong-communication-service",
                payload_object.sha256,
            ),
            effect_id=delivery_effect.effect_id,
            request_id=request_id,
            run_id=run_id,
            generation=generation.generation,
            destination_component_id="tiangong-communication-service",
            intent_kind="DELIVERY",
            payload_object_id=payload_object.object_id,
            payload_sha256=payload_object.sha256,
            created_at_ms=delivery_now,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        self._advance(
            "delivery",
            delivery_entity,
            "PLANNED",
            now_ms=delivery_now,
            outbox=(outbox,),
        )
        self._advance("request", request_id, "DELIVERING", now_ms=delivery_now)

    def close(self) -> None:
        self._closed.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("orchestration worker did not stop")


__all__ = [
    "GatewayOrchestrationWorker",
    "OrchestrationError",
    "compatibility_capability_manifest",
]
