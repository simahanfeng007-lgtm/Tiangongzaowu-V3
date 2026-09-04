"""Gateway-owned one-time Omni capability authority.

The frozen backend may propose an action, target and arguments. It cannot mint,
weaken or simulate authority. Every call is rebound to an active generation,
re-evaluated by PolicyEngine and receives a short-lived signed grant.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import threading
import time
import unicodedata
import urllib.parse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping

from contracts import (
    ActionImpact,
    ActionIntent,
    ActionRegistrySnapshot,
    ExecutionTicket,
    ExecutionTicketPayload,
    OmniCapabilityGrant,
    PolicyDecision,
    ResourceEnvelope,
    TrustBundle,
    canonical_sha256,
    derive_effect_identity,
    derive_run_identity,
)

from .action_registry import ActionRegistryError, ActionSchemaCatalog
from .composition_activation_adapter import (
    CompositionActivationAdapterError,
    materialize_static_root_step,
)
from .composition_execution_binding import (
    COMPOSITION_STEP_PIPELINE_VERSION,
    CompositionExecutionBindingError,
    derive_composition_execution_binding,
)
from .composition_step_authorization import (
    CompositionStepAuthorizationArtifacts,
    CompositionStepAuthorizationRequest,
)
from .effects import EffectClaim, EffectResult
from .grant_signer import issue_omni_capability_grant
from .gateway_url import DEFAULT_GATEWAY_URL, normalize_gateway_url
from .impact_evaluator import compute_action_impact, derive_impact_knobs, probe_target_state
from .policy_engine import PolicyEngine, SourceRef
from .policy_evidence import PolicyEvidenceLedger
from .store import StoreCasConflict, StoreConflictError
from .tickets import TicketSigner


_AUTHORITY_FIELDS = frozenset(
    {
        "confirm",
        "confirmed",
        "confirmation",
        "confirmation_id",
        "confirmation_sha256",
        "allow_shell",
        "allow_python",
        "allow_absolute_paths",
        "__capability_grant",
        "__runtime",
        # D-10: a model may never self-report the risk class of its own call.
        "risk",
        # D-08: a model may propose, never assert, provenance/authorization facts.
        "source_ref",
        "source_refs",
        "source_type",
        "provenance",
        "authorization",
        "authorized",
    }
)
_PATH_KEYS = frozenset(
    {
        "path",
        "paths",
        "file",
        "filename",
        "file_path",
        "filepath",
        "input_file",
        "input_path",
        "source",
        "source_file",
        "source_path",
        "src",
        "destination",
        "destination_file",
        "destination_path",
        "dst",
        "output",
        "output_file",
        "output_path",
        "output_dir",
        "save_as",
        "target_path",
        "template_file",
        "template_path",
        "attachment",
        "attachments",
        "attachment_path",
        "directory",
        "dir",
        "folder",
        "root_path",
        "cwd",
        "workspace",
        "project_dir",
        "database_path",
        "db_path",
        "archive",
    }
)
_URL_SCHEMES = frozenset({"http", "https", "data"})
_SAFE_COMPOSITION_SIDE_EFFECTS = frozenset({"none", "read"})
_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}")
_OBJECT_REFERENCE_RE = re.compile(r"oref_[0-9a-f]{64}")
_COMMON_FILE_SUFFIXES = frozenset(
    {
        ".css", ".csv", ".db", ".docx", ".gif", ".html", ".jpeg",
        ".jpg", ".js", ".json", ".log", ".md", ".pdf", ".png",
        ".pptx", ".py", ".sql", ".sqlite", ".ts", ".tsv", ".txt",
        ".webp", ".xlsx", ".xml", ".yaml", ".yml", ".zip",
    }
)

# D-21 用户指定即授权（owner 2026-07-29 决策，与后端 permission_settings 直通语义对齐）：
# 用户在本轮消息里明确指定的路径（字面绝对路径 / 桌面·文档·下载别名），其写入
# 视为已授权，不再被工作区围栏拦截。提取是确定性的（不认模型自报，只认用户原文）。
_USER_PATH_ABS_RE = re.compile(
    r"[a-zA-Z]:[\\/][^\s\"'<>|*?，。；、！？（）()【】\[\]{}《》“”‘’]*"
)
_USER_FOLDER_ALIASES = {
    "桌面": "Desktop", "desktop": "Desktop",
    "文档": "Documents", "documents": "Documents",
    "下载": "Downloads", "downloads": "Downloads",
}
# 硬禁区：即使用户点名也一律拒绝（与后端 A5 区/凭据区口径一致）。
_HARD_DENY_DIR_PARTS = frozenset({".ssh", ".aws", ".gnupg", ".azure", ".config"})
_HARD_DENY_PREFIXES = (
    os.path.normcase(os.environ.get("SystemRoot") or r"C:\Windows"),
    os.path.normcase(os.environ.get("ProgramFiles") or r"C:\Program Files"),
    os.path.normcase(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"),
    os.path.normcase(os.environ.get("ProgramData") or r"C:\ProgramData"),
)


def _is_hard_deny_path(resolved: Path) -> bool:
    text = os.path.normcase(str(resolved))
    for prefix in _HARD_DENY_PREFIXES:
        if text == prefix or text.startswith(prefix + os.sep):
            return True
    # 盘符根（C:\ 本身）
    if len(text) <= 3:
        return True
    if any(part.casefold() in _HARD_DENY_DIR_PARTS for part in resolved.parts):
        return True
    return resolved.name.casefold().endswith(".env")


def _user_specified_roots_from_text(text: str) -> tuple[Path, ...]:
    """从用户本轮原文提取指定路径根（字面绝对路径 + 已知目录别名）。"""
    roots: list[Path] = []
    home = Path.home()
    lowered = text.casefold()
    for alias, folder in _USER_FOLDER_ALIASES.items():
        if alias.casefold() in lowered:
            roots.append((home / folder).resolve(strict=False))
    for match in _USER_PATH_ABS_RE.finditer(text):
        raw = match.group(0).rstrip(".,;:!?)]}，。；、！？）】》」』\"'“”‘’\\/ \t")
        if len(raw) < 3:
            continue
        try:
            candidate = Path(raw.replace("/", "\\")).resolve(strict=False)
        except OSError:
            continue
        if candidate.is_absolute():
            roots.append(candidate)
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return tuple(deduped)

# D-06 统一 admission：子 effect 与父级共用 effect 台账 + security_nonce_ledger。
_OMNI_SUB_EFFECT_PIPELINE_VERSION = "tiangong.omni-grant-authority.v1"
_OMNI_NONCE_CONSUMER_ID = "tiangong-total-gateway:omni-grant-authority"
# run_sequence 探测上限：run_id 由 derive_run_identity(request_id, seq) 派生，
# 超过该上界的 run 视为合成/越界身份，fail-closed。
_RUN_SEQUENCE_PROBE_LIMIT = 256


class OmniGrantAuthorityError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ActiveExecutionAuthority:
    ticket: ExecutionTicket
    life_id: str
    life_evidence_ref: str
    workspace_scope_hash: str
    session_id: str
    registered_at_ms: int
    authority_expires_at_ms: int


class OmniGrantAuthority:
    def __init__(
        self,
        *,
        registry: ActionRegistrySnapshot,
        capability_manifest_hash: str,
        component_manifest_hash: str,
        skill_catalog_hash: str,
        signer: TicketSigner,
        gateway_epoch: int,
        workspace_root: Path,
        evidence: PolicyEvidenceLedger,
        trust_bundle_provider: Callable[[int], TrustBundle],
        effect_store,
        action_schema_catalog: ActionSchemaCatalog | None = None,
        object_store=None,
        capability_source_manifest_hash: str | None = None,
        parent_capability_manifest_hash: str | None = None,
        composition_capability_manifest_hash: str | None = None,
        gateway_url: str = DEFAULT_GATEWAY_URL,
    ) -> None:
        if not registry.has_valid_sha256():
            raise ValueError("Omni action registry digest is invalid")
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (capability_manifest_hash, component_manifest_hash, skill_catalog_hash)
        ):
            raise ValueError("Omni authority manifest binding is invalid")
        if not workspace_root.is_absolute() or not workspace_root.is_dir() or workspace_root.is_symlink():
            raise ValueError("Omni authority workspace is unsafe")
        if effect_store is None or not all(
            callable(getattr(effect_store, name, None))
            for name in ("get_effect", "list_effect_facts", "admit_sub_effect", "action_fence_status")
        ):
            raise ValueError("Omni authority requires the gateway effect ledger store")
        self.registry = registry
        if action_schema_catalog is not None and (
            not isinstance(action_schema_catalog, ActionSchemaCatalog)
            or not action_schema_catalog.has_valid_sha256()
            or action_schema_catalog.source_manifest_sha256
            != registry.source_manifest_sha256
        ):
            raise ValueError("Omni action schema authority is invalid")
        if object_store is not None and not all(
            callable(getattr(object_store, name, None))
            for name in ("get_reference", "read_bytes")
        ):
            raise ValueError("Omni object authority is invalid")
        source_manifest_hash = (
            capability_manifest_hash
            if capability_source_manifest_hash is None
            else capability_source_manifest_hash
        )
        if re.fullmatch(r"[0-9a-f]{64}", source_manifest_hash) is None:
            raise ValueError("Omni capability source manifest binding is invalid")
        parent_manifest_hash = (
            capability_manifest_hash
            if parent_capability_manifest_hash is None
            else parent_capability_manifest_hash
        )
        composition_manifest_hash = (
            capability_manifest_hash
            if composition_capability_manifest_hash is None
            else composition_capability_manifest_hash
        )
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (parent_manifest_hash, composition_manifest_hash)
        ):
            raise ValueError("Omni composition manifest binding is invalid")
        self.capability_manifest_hash = capability_manifest_hash
        self.capability_source_manifest_hash = source_manifest_hash
        # These are deliberately distinct authorities.  The parent ticket is
        # issued against the Gateway compatibility manifest, while a
        # composition child executes one real registry Action under the
        # compiled composition execution manifest.  The raw source-file hash
        # remains drift evidence only and is never presented as a
        # ``CapabilityManifest.sha256`` at the execution boundary.
        self.parent_capability_manifest_hash = parent_manifest_hash
        self.composition_capability_manifest_hash = composition_manifest_hash
        self.component_manifest_hash = component_manifest_hash
        self.skill_catalog_hash = skill_catalog_hash
        self.signer = signer
        self.gateway_epoch = gateway_epoch
        self.workspace_root = workspace_root.resolve(strict=True)
        self.workspace_id = "workspace-" + canonical_sha256(str(self.workspace_root))
        self.workspace_scope_hash = self._workspace_scope_hash(self.workspace_root)
        self.evidence = evidence
        self.trust_bundle_provider = trust_bundle_provider
        # D-06：子 effect 台账（gateway.sqlite3），统一 admission 的唯一写入面。
        self._effect_store = effect_store
        self._action_schema_catalog = action_schema_catalog
        self._object_store = object_store
        self.gateway_url = normalize_gateway_url(gateway_url)
        self._active: dict[str, ActiveExecutionAuthority] = {}
        self._issued: dict[tuple[str, str], tuple[str, int, dict[str, Any]]] = {}
        self._call_locks: dict[tuple[str, str], threading.Lock] = {}
        self._composition_locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _workspace_scope_hash(workspace: Path) -> str:
        normalized = os.path.normcase(
            unicodedata.normalize("NFC", str(workspace.resolve(strict=True)))
        )
        return canonical_sha256({"normalized_workspace": normalized})

    @staticmethod
    def _invocation_hash(action: str, target: str, args: Mapping[str, Any]) -> str:
        return canonical_sha256({"action": action, "args": dict(args), "target": target})

    @staticmethod
    def _derive_run_sequence(request_id: str, run_id: str) -> int:
        """由 run_id 反解 run_sequence（派生是单向哈希，按有界区间探测，fail-closed）。"""
        for candidate in range(1, _RUN_SEQUENCE_PROBE_LIMIT + 1):
            if derive_run_identity(request_id, candidate).run_id == run_id:
                return candidate
        raise OmniGrantAuthorityError("omni.run_identity.unbound", status=409)

    def _sub_effect_identity(self, outer, *, call_id: str, action: str, target: str, arguments_sha256: str):
        """D-06：确定性子 effect 身份。

        幂等键 = (parent_ticket, call_id, action/target/args, scope 四元组)，
        不含任何时间戳/随机数 —— 60s 内存缓存过期或网关重启后，同 call_id
        必得同一 effect_id（台账幂等命中），绝不产生新 effect。
        """
        run_sequence = self._derive_run_sequence(outer.request_id, outer.run_id)
        sub_intent_sha256 = canonical_sha256(
            {
                "domain": "tiangong.omni.sub-effect-intent.v1",
                "parent_ticket_id": outer.ticket_id,
                "call_id": call_id,
                "action": action,
                "target": target,
                "arguments_sha256": arguments_sha256,
                "workspace_id": self.workspace_id,
                "request_id": outer.request_id,
                "run_id": outer.run_id,
                "generation": outer.generation,
            }
        )
        identity = derive_effect_identity(
            request_id=outer.request_id,
            run_id=outer.run_id,
            run_sequence=run_sequence,
            generation=outer.generation,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=sub_intent_sha256,
        )
        return run_sequence, sub_intent_sha256, identity.effect_id

    def _recorded_admission_response(self, effect_id: str) -> dict[str, Any] | None:
        """台账中已提交的首响应（幂等重放来源）；无 receipt 响应返回 None。"""
        facts = self._effect_store.list_effect_facts(effect_id)
        for fact in reversed(facts):
            if fact["fact_kind"] == "RECEIPT":
                recorded = json.loads(fact["payload_json"]).get("omni_admission_response")
                return deepcopy(recorded) if recorded is not None else None
        return None

    def register(
        self,
        ticket: ExecutionTicket,
        *,
        life_id: str,
        life_evidence_ref: str,
        session_id: str,
        registered_at_ms: int,
        authority_expires_at_ms: int,
    ) -> None:
        if (
            not life_id
            or not re.fullmatch(r"lev_[0-9a-f]{64}", life_evidence_ref)
            or authority_expires_at_ms <= registered_at_ms
            or ticket.payload.workspace_id != self.workspace_id
        ):
            raise ValueError("active Omni execution authority is invalid")
        active = ActiveExecutionAuthority(
            ticket=ticket,
            life_id=life_id,
            life_evidence_ref=life_evidence_ref,
            workspace_scope_hash=self.workspace_scope_hash,
            session_id=session_id,
            registered_at_ms=registered_at_ms,
            authority_expires_at_ms=authority_expires_at_ms,
        )
        with self._lock:
            existing = self._active.get(ticket.payload.ticket_id)
            if existing is not None and existing != active:
                raise OmniGrantAuthorityError("omni.active_ticket.conflict", status=409)
            self._active[ticket.payload.ticket_id] = active

    def unregister(self, ticket_id: str) -> None:
        with self._lock:
            self._active.pop(ticket_id, None)
            stale = [key for key in self._issued if key[0] == ticket_id]
            for key in stale:
                self._issued.pop(key, None)
            stale_locks = [key for key in self._call_locks if key[0] == ticket_id]
            for key in stale_locks:
                self._call_locks.pop(key, None)
            stale_composition_locks = [
                key for key in self._composition_locks if key[0] == ticket_id
            ]
            for key in stale_composition_locks:
                self._composition_locks.pop(key, None)

    def _get_active(self, payload: Mapping[str, Any], now_ms: int) -> ActiveExecutionAuthority:
        ticket_id = str(payload.get("ticket_id") or "")
        with self._lock:
            active = self._active.get(ticket_id)
        if active is None:
            raise OmniGrantAuthorityError("omni.active_ticket.missing", status=409)
        outer = active.ticket.payload
        if (
            now_ms > active.authority_expires_at_ms
            or str(payload.get("request_id") or "") != outer.request_id
            or str(payload.get("run_id") or "") != outer.run_id
            or type(payload.get("generation")) is not int
            or payload.get("generation") != outer.generation
            or str(payload.get("principal_scope_hash") or "") != outer.principal_scope_hash
        ):
            raise OmniGrantAuthorityError("omni.active_ticket.binding_invalid", status=409)
        return active

    @staticmethod
    def _validate_no_authority_fields(value: Any, path: str = "args") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                normalized = unicodedata.normalize("NFKC", key_text).casefold().replace("-", "_")
                if normalized in _AUTHORITY_FIELDS or normalized.startswith("__"):
                    raise OmniGrantAuthorityError(
                        f"omni.model_authority_field.forbidden:{path}.{key_text}"
                    )
                OmniGrantAuthority._validate_no_authority_fields(item, f"{path}.{key_text}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                OmniGrantAuthority._validate_no_authority_fields(item, f"{path}[{index}]")


    @staticmethod
    def _contains_destructive_overwrite(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).casefold().replace("-", "_")
                if normalized in {"overwrite", "replace_existing", "delete_existing", "truncate_existing"} and item is True:
                    return True
                if OmniGrantAuthority._contains_destructive_overwrite(item):
                    return True
        elif isinstance(value, list):
            return any(OmniGrantAuthority._contains_destructive_overwrite(item) for item in value)
        return False

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        parsed = urllib.parse.urlsplit(value)
        return parsed.scheme.casefold() in _URL_SCHEMES and bool(parsed.scheme)

    def _validate_path_value(
        self,
        value: str,
        *,
        allow_absolute: bool,
        allow_url: bool = False,
        user_roots: tuple[Path, ...] = (),
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if self._looks_like_url(text):
            if allow_url:
                return
            raise OmniGrantAuthorityError("omni.path.url_forbidden")

        windows_candidate = PureWindowsPath(text)
        windows_absolute = bool(
            windows_candidate.is_absolute()
            or windows_candidate.drive
            or text.startswith("\\\\")
        )
        if windows_absolute and os.name != "nt":
            raise OmniGrantAuthorityError("omni.path.absolute_forbidden")

        # Backslashes are path separators for tools that run on Windows even
        # when policy validation is executed on another build platform.
        normalized_text = text.replace("\\", os.sep)
        candidate = Path(normalized_text).expanduser()
        resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (self.workspace_root / candidate).resolve(strict=False)
        # A5 sovereign locations remain forbidden regardless of workspace or
        # signed path scope.  Everything else may be addressed by an A1-A4
        # action when its machine-generated permission carries path freedom.
        if _is_hard_deny_path(resolved):
            raise OmniGrantAuthorityError("omni.path.workspace_escape")
        try:
            resolved.relative_to(self.workspace_root)
            # 工作区内：绝对形式仍需动作级 allow_absolute（原语义不变）
            if candidate.is_absolute() and not allow_absolute:
                raise OmniGrantAuthorityError("omni.path.absolute_forbidden")
            return
        except ValueError:
            pass
        # D-21：用户本轮明确指定的路径即授权（硬禁区除外，与后端直通语义一致）。
        if user_roots:
            for root in user_roots:
                try:
                    resolved.relative_to(root)
                    return
                except ValueError:
                    continue
        # `allow_absolute` names the signed unrestricted-location capability;
        # it also covers a relative path that deliberately traverses out of the
        # active workspace.  The resolved target is still bound into the grant.
        if allow_absolute:
            return
        if candidate.is_absolute():
            raise OmniGrantAuthorityError("omni.path.absolute_forbidden")
        raise OmniGrantAuthorityError("omni.path.workspace_escape")

    def _user_specified_roots(self, request_id: str) -> tuple[Path, ...]:
        """当前请求用户原文里的指定路径根；取不到用户文本时为空（fail-closed）。"""
        get_envelope = getattr(self._effect_store, "get_request_envelope", None)
        if not callable(get_envelope):
            return ()
        try:
            envelope = get_envelope(request_id)
        except Exception:
            return ()
        text = str(getattr(envelope, "text", "") or "") if envelope is not None else ""
        if not text.strip():
            return ()
        return _user_specified_roots_from_text(text)

    def _validate_paths(self, target: str, args: Mapping[str, Any], *, allow_absolute: bool, user_roots: tuple[Path, ...] = ()) -> None:
        if target:
            self._validate_path_value(
                target,
                allow_absolute=allow_absolute,
                allow_url=True,
                user_roots=user_roots,
            )

        def visit(value: Any, key: str = "") -> None:
            if isinstance(value, Mapping):
                for nested_key, nested_value in value.items():
                    normalized_key = unicodedata.normalize("NFKC", str(nested_key)).casefold().replace("-", "_")
                    visit(nested_value, normalized_key)
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, key)
                return
            if isinstance(value, str) and key in _PATH_KEYS:
                self._validate_path_value(
                    value,
                    allow_absolute=allow_absolute,
                    allow_url=False,
                    user_roots=user_roots,
                )

        visit(args)

    @staticmethod
    def _composition_path_strings(value: Any, *, path_context: bool = False):
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = unicodedata.normalize("NFKC", str(key)).casefold().replace("-", "_")
                yield from OmniGrantAuthority._composition_path_strings(
                    item,
                    path_context=path_context or normalized in _PATH_KEYS,
                )
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from OmniGrantAuthority._composition_path_strings(
                    item, path_context=path_context
                )
        elif path_context and isinstance(value, str) and value.strip():
            yield value.strip()

    def _composition_target_looks_pathlike(
        self, value: str, *, object_ids: frozenset[str]
    ) -> bool:
        text = value.strip()
        if not text or text in object_ids or _OBJECT_REFERENCE_RE.fullmatch(text):
            return False
        if (
            text in {".", ".."}
            or "/" in text
            or "\\" in text
            or self._looks_like_url(text)
            or PureWindowsPath(text).is_absolute()
            or bool(PureWindowsPath(text).drive)
            or Path(text).is_absolute()
            or Path(text).suffix.casefold() in _COMMON_FILE_SUFFIXES
        ):
            return True
        try:
            return (self.workspace_root / text).exists()
        except OSError:
            return True

    def _validate_composition_workspace_path(self, value: str) -> None:
        text = value.strip()
        if not text or self._looks_like_url(text):
            raise OmniGrantAuthorityError(
                "composition.authorization.path_policy_exceeded", status=403
            )
        windows = PureWindowsPath(text)
        if os.name != "nt" and (windows.is_absolute() or windows.drive):
            raise OmniGrantAuthorityError(
                "composition.authorization.path_policy_exceeded", status=403
            )
        try:
            normalized = text.replace("\\", os.sep)
            candidate = Path(normalized).expanduser()
            resolved = (
                candidate.resolve(strict=False)
                if candidate.is_absolute()
                else (self.workspace_root / candidate).resolve(strict=False)
            )
            if _is_hard_deny_path(resolved):
                raise ValueError("hard-deny path")
            resolved.relative_to(self.workspace_root)
        except (OSError, ValueError) as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.path_policy_exceeded", status=403
            ) from exc

    def _validate_composition_path_policy(
        self,
        *,
        target: str,
        args: Mapping[str, Any],
        path_policy: str,
        object_ids: frozenset[str],
    ) -> None:
        path_values = tuple(self._composition_path_strings(args))
        if path_policy == "no_path":
            if target or path_values:
                raise OmniGrantAuthorityError(
                    "composition.authorization.path_policy_exceeded", status=403
                )
            return
        if path_policy == "object_grant_only":
            if any(value not in object_ids for value in path_values):
                raise OmniGrantAuthorityError(
                    "composition.authorization.object_grant_path_required", status=403
                )
            if self._composition_target_looks_pathlike(
                target, object_ids=object_ids
            ):
                raise OmniGrantAuthorityError(
                    "composition.authorization.object_grant_path_required", status=403
                )
            return
        if path_policy == "workspace_only":
            for value in path_values:
                if value not in object_ids:
                    self._validate_composition_workspace_path(value)
            if self._composition_target_looks_pathlike(
                target, object_ids=object_ids
            ):
                self._validate_composition_workspace_path(target)
            return
        raise OmniGrantAuthorityError(
            "composition.authorization.path_policy_invalid", status=403
        )

    def _composition_dependencies(self) -> tuple[ActionSchemaCatalog, Any, Any]:
        catalog = self._action_schema_catalog
        store = self._effect_store
        objects = self._object_store
        if (
            catalog is None
            or objects is None
            or not callable(
                getattr(store, "get_active_executable_composition_plan", None)
            )
            or not callable(
                getattr(store, "get_composition_step_authorization", None)
            )
            or not callable(
                getattr(store, "commit_composition_step_authorization", None)
            )
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.authority_unavailable", status=503
            )
        if (
            not catalog.has_valid_sha256()
            or catalog.source_manifest_sha256 != self.registry.source_manifest_sha256
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.schema_authority_invalid", status=409
            )
        return catalog, store, objects

    def _composition_parent(
        self, *, parent_ticket_id: str, plan: Any, now_ms: int
    ) -> ActiveExecutionAuthority:
        with self._lock:
            active = self._active.get(parent_ticket_id)
        if active is None:
            raise OmniGrantAuthorityError(
                "composition.authorization.parent_ticket_missing", status=409
            )
        outer = active.ticket.payload
        try:
            signature_is_current = self.signer.sign_execution(outer) == active.ticket
            workspace = Path(plan.workspace.workspace_root).expanduser().resolve(
                strict=True
            )
        except (OSError, TypeError, ValueError) as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.parent_ticket_invalid", status=409
            ) from exc
        if (
            not signature_is_current
            or outer.ticket_id != parent_ticket_id
            or outer.gateway_epoch != self.gateway_epoch
            or outer.capability_manifest_hash
            != self.parent_capability_manifest_hash
            or outer.component_manifest_hash != self.component_manifest_hash
            or outer.request_id != plan.request_id
            or outer.run_id != plan.run_id
            or outer.generation != plan.generation
            or outer.principal_scope_hash != plan.principal_scope_hash
            or outer.workspace_id != self.workspace_id
            or plan.workspace.workspace_id != self.workspace_id
            or plan.workspace.workspace_scope_sha256 != self.workspace_scope_hash
            or workspace != self.workspace_root
            or plan.registration_id == ""
            or not outer.issued_at_ms <= now_ms
            or not outer.not_before_ms <= now_ms < outer.expires_at_ms
            or not active.registered_at_ms <= now_ms < active.authority_expires_at_ms
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.parent_ticket_invalid", status=409
            )
        return active

    def _composition_object_grants(
        self, *, plan: Any, active: ActiveExecutionAuthority, objects: Any
    ) -> tuple[Any, ...]:
        grants = tuple(
            sorted(
                (
                    item.object_grant
                    for item in plan.plan_inputs
                    if item.input_kind == "OBJECT_GRANT"
                    and item.object_grant is not None
                ),
                key=lambda item: (item.object_id, item.revision),
            )
        )
        if len({(item.object_id, item.revision) for item in grants}) != len(grants):
            raise OmniGrantAuthorityError(
                "composition.authorization.object_grant_invalid", status=409
            )
        outer_grants = {
            (item.object_id, item.revision): item
            for item in active.ticket.payload.input_objects
        }
        try:
            for grant in grants:
                if outer_grants.get((grant.object_id, grant.revision)) != grant:
                    raise ValueError("outer ObjectGrant mismatch")
                reference = objects.get_reference(grant.object_id)
                if (
                    reference is None
                    or not reference.has_valid_sha256()
                    or reference.object_id != grant.object_id
                    or reference.sha256 != grant.sha256
                    or reference.size_bytes != grant.size_bytes
                    or reference.tenant_id != grant.tenant_id
                    or reference.link_account_id != grant.link_account_id
                    or reference.conversation_scope_hash
                    != grant.conversation_scope_hash
                ):
                    raise ValueError("ObjectReference mismatch")
                body = objects.read_bytes(grant.object_id)
                if (
                    len(body) != grant.size_bytes
                    or hashlib.sha256(body).hexdigest() != grant.sha256
                ):
                    raise ValueError("object bytes mismatch")
        except Exception as exc:
            if isinstance(exc, OmniGrantAuthorityError):
                raise
            raise OmniGrantAuthorityError(
                "composition.authorization.object_grant_invalid", status=409
            ) from exc
        return grants

    def _record_composition_evidence(
        self,
        record: Any,
        permission: Any,
        active: ActiveExecutionAuthority,
    ) -> None:
        try:
            intent, impact, decision, ticket, grant = (
                record.artifacts.restore_contracts()
            )
            runtime = record.artifacts.runtime_response["runtime"]
            if (
                self.signer.sign_execution(ticket.payload) != ticket
                or self.signer.sign_omni_capability(grant.payload) != grant
                or runtime["gateway_url"] != self.gateway_url
                or runtime["session_id"] != active.session_id
                or runtime["gateway_epoch"] != self.gateway_epoch
                or runtime["capability_manifest_hash"]
                != self.composition_capability_manifest_hash
                or runtime["component_manifest_hash"]
                != self.component_manifest_hash
            ):
                raise ValueError("stored signature does not match current authority")
            self.evidence.record_evaluation(
                intent=intent,
                impact=impact,
                permission=permission,
                registry=self.registry,
                decision=decision,
                ticket=ticket,
                grant=grant,
                observed_at_ms=record.committed_at_ms,
            )
        except Exception as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.evidence_invalid", status=503
            ) from exc

    def issue_composition_step(
        self,
        *,
        parent_ticket_id: str,
        registration_id: str,
        step_id: str,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        key = (parent_ticket_id, registration_id, step_id)
        if any(
            not isinstance(value, str) or _OPAQUE_ID_RE.fullmatch(value) is None
            for value in key
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.identity_invalid"
            )
        with self._lock:
            call_lock = self._composition_locks.setdefault(key, threading.Lock())
        with call_lock:
            return self._issue_composition_step_once(
                parent_ticket_id=parent_ticket_id,
                registration_id=registration_id,
                step_id=step_id,
                now_ms=now_ms,
            )

    def _issue_composition_step_once(
        self,
        *,
        parent_ticket_id: str,
        registration_id: str,
        step_id: str,
        now_ms: int | None,
    ) -> dict[str, Any]:
        now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        if type(now) is not int or now < 0:
            raise OmniGrantAuthorityError("composition.authorization.time_invalid")
        catalog, store, objects = self._composition_dependencies()
        try:
            plan_record = store.get_active_executable_composition_plan(
                registration_id, now_ms=now
            )
        except Exception as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.plan_unavailable", status=409
            ) from exc
        if plan_record is None:
            raise OmniGrantAuthorityError(
                "composition.authorization.plan_not_active", status=409
            )
        plan = plan_record.executable_plan
        if (
            plan.registration_id != registration_id
            or not plan.has_valid_identity()
            or not plan.sealed_at_ms <= now < plan.expires_at_ms
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.plan_invalid", status=409
            )
        try:
            materialized = materialize_static_root_step(plan, step_id=step_id)
        except CompositionActivationAdapterError as exc:
            raise OmniGrantAuthorityError(exc.code, status=409) from exc
        active = self._composition_parent(
            parent_ticket_id=parent_ticket_id, plan=plan, now_ms=now
        )
        outer = active.ticket.payload

        if (
            plan.action_registry_sha256 != self.registry.registry_sha256
            or plan.capability_manifest_sha256
            != self.capability_source_manifest_hash
            or self.capability_source_manifest_hash
            != self.registry.source_manifest_sha256
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.current_manifest_mismatch", status=409
            )
        permission = next(
            (
                item
                for item in self.registry.permissions
                if item.action_id == materialized.step.action_id
                and item.action_version == materialized.step.action_version
            ),
            None,
        )
        if (
            permission is None
            or not permission.has_valid_sha256()
            or permission != materialized.step.permission
            or permission.permission_sha256
            != materialized.step.permission_sha256
            or permission.registry_risk != "A0"
            or permission.effective_risk != "A0"
            or permission.effect not in {"read", "verify"}
            or not set(permission.allowed_side_effects).issubset(
                _SAFE_COMPOSITION_SIDE_EFFECTS
            )
            or permission.allow_shell
            or permission.allow_python
            or permission.requires_confirmation
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.a0_ceiling_exceeded", status=403
            )
        try:
            schema = catalog.resolve(
                permission.action_id,
                permission.action_version,
                expected_sha256=materialized.step.argument_schema_sha256,
                require_explicit=True,
            )
        except ActionRegistryError as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.schema_invalid", status=409
            ) from exc

        self._validate_no_authority_fields(materialized.arguments)
        if self._contains_destructive_overwrite(materialized.arguments):
            raise OmniGrantAuthorityError(
                "composition.authorization.destructive_arguments_forbidden",
                status=403,
            )
        grants = self._composition_object_grants(
            plan=plan, active=active, objects=objects
        )
        object_ids = frozenset(item.object_id for item in grants)
        self._validate_composition_path_policy(
            target=materialized.target,
            args=materialized.arguments,
            path_policy=permission.path_policy,
            object_ids=object_ids,
        )
        try:
            validated = schema.validate_exact(
                permission.action_id,
                materialized.target,
                materialized.arguments,
                workspace=self.workspace_root,
                available_actions=(item.action_id for item in self.registry.permissions),
                user_roots=(),
            )
        except ActionRegistryError as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.arguments_invalid", status=409
            ) from exc
        if (
            validated.get("action") != permission.action_id
            or validated.get("target") != materialized.target
            or validated.get("args") != materialized.arguments
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.arguments_normalized", status=409
            )

        materialized_arguments_sha256 = canonical_sha256(materialized.arguments)
        arguments_sha256 = self._invocation_hash(
            permission.action_id, materialized.target, materialized.arguments
        )
        target_ref = (
            "target-"
            + canonical_sha256(
                {"action": permission.action_id, "target": materialized.target}
            )
            if materialized.target
            else None
        )
        target_state = probe_target_state(materialized.target, self.workspace_root)
        target_snapshot_sha256 = (
            None if target_state is None else canonical_sha256(target_state)
        )
        try:
            derived_binding = derive_composition_execution_binding(
                plan,
                materialized,
                parent_ticket_id=parent_ticket_id,
                workspace_id=self.workspace_id,
                workspace_scope_hash=self.workspace_scope_hash,
                target_snapshot_sha256=target_snapshot_sha256,
            )
        except CompositionExecutionBindingError as exc:
            raise OmniGrantAuthorityError(exc.code, status=409) from exc
        run_sequence = derived_binding.run_sequence
        ordinal = derived_binding.ordinal
        effect_intent_sha256 = derived_binding.effect_intent_sha256
        effect_id = derived_binding.effect_id
        binding = derived_binding.binding

        fence_status = store.action_fence_status()
        raw_fence_epoch = int(fence_status.get("action_fence_epoch", -1))
        if (
            raw_fence_epoch != 0
            or bool(fence_status.get("fenced"))
            or bool(fence_status.get("draining"))
        ):
            raise OmniGrantAuthorityError(
                "composition.authorization.action_fenced", status=409
            )
        authorization_ceiling_ms = min(
            plan.expires_at_ms,
            outer.expires_at_ms,
            active.authority_expires_at_ms,
        )
        expires_at_ms = min(now + 60_000, authorization_ceiling_ms)
        if now >= expires_at_ms:
            raise OmniGrantAuthorityError(
                "composition.authorization.expired", status=409
            )
        object_grant_values = tuple(
            item.model_dump(mode="json") for item in grants
        )
        parent_ticket_sha256 = canonical_sha256(
            active.ticket.model_dump(mode="json")
        )
        request = CompositionStepAuthorizationRequest.build(
            registration_id=registration_id,
            registration_sha256=plan.registration_sha256,
            executable_plan_id=plan.executable_plan_id,
            executable_plan_sha256=plan.executable_plan_sha256,
            composition_plan_id=plan.composition_plan_id,
            composition_plan_sha256=plan.composition_plan_sha256,
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
            principal_scope_hash=plan.principal_scope_hash,
            parent_ticket_id=parent_ticket_id,
            parent_ticket_sha256=parent_ticket_sha256,
            parent_ticket_expires_at_ms=outer.expires_at_ms,
            step_id=step_id,
            step_binding_sha256=materialized.step.sha256,
            attempt=1,
            action_id=permission.action_id,
            action_version=permission.action_version,
            source_revision_sha256=canonical_sha256(
                materialized.step.source_revision.model_dump(mode="json")
            ),
            action_registry_sha256=self.registry.registry_sha256,
            action_permission_sha256=permission.permission_sha256,
            argument_schema_sha256=schema.argument_schema_sha256,
            result_schema_sha256=materialized.step.result_schema_sha256,
            composition_binding_sha256=binding.binding_sha256,
            materialized_arguments=materialized.arguments,
            target=materialized.target,
            target_ref=target_ref,
            target_snapshot=target_state,
            workspace_id=self.workspace_id,
            workspace_scope_sha256=self.workspace_scope_hash,
            object_grants=object_grant_values,
            prebound_effect_id=effect_id,
            prebound_effect_intent_sha256=effect_intent_sha256,
            action_fence_epoch=raw_fence_epoch,
            issued_at_ms=now,
            expires_at_ms=expires_at_ms,
            authorization_ceiling_ms=authorization_ceiling_ms,
        )

        try:
            existing = store.get_composition_step_authorization(
                plan.executable_plan_id, step_id, attempt=1, now_ms=now
            )
        except Exception as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.replay_invalid", status=409
            ) from exc
        if existing is not None:
            if (
                existing.request.authorization_request_sha256
                != request.authorization_request_sha256
            ):
                raise OmniGrantAuthorityError(
                    "composition.authorization.replay_conflict", status=409
                )
            try:
                persisted, _ = store.commit_composition_step_authorization(
                    request,
                    parent_ticket=active.ticket,
                    artifacts=existing.artifacts,
                    now_ms=now,
                )
            except Exception as exc:
                raise OmniGrantAuthorityError(
                    "composition.authorization.replay_invalid", status=409
                ) from exc
            self._record_composition_evidence(persisted, permission, active)
            return persisted.runtime_response

        try:
            trust_bundle = self.trust_bundle_provider(now)
        except Exception as exc:
            raise OmniGrantAuthorityError(
                "omni.trust_bundle.unavailable", status=503
            ) from exc
        if (
            trust_bundle.production_ready is not True
            or trust_bundle.gateway_epoch != self.gateway_epoch
            or not trust_bundle.has_valid_sha256()
        ):
            raise OmniGrantAuthorityError("omni.trust_bundle.invalid", status=503)

        resources = ResourceEnvelope(
            max_runtime_ms=min(outer.max_runtime_ms, 3_600_000),
            max_output_bytes=outer.max_output_bytes,
            max_tool_calls=1,
        )
        authorization_source_refs = tuple(
            sorted(
                (
                    SourceRef(
                        source_type="CURRENT_USER_INSTRUCTION",
                        object_id=outer.ticket_id,
                        object_revision=1,
                        sha256=canonical_sha256(
                            active.ticket.payload.model_dump(mode="json")
                        ),
                    ),
                    SourceRef(
                        source_type="PREAUTHORIZED_USER_FACT",
                        object_id=active.life_evidence_ref,
                        object_revision=1,
                        sha256=active.life_evidence_ref[4:],
                    ),
                ),
                key=lambda ref: ref.sort_key(),
            )
        )
        intent = ActionIntent(
            intent_id="intent-composition-" + canonical_sha256(
                {
                    "effect_id": effect_id,
                    "binding_sha256": binding.binding_sha256,
                    "created_at_ms": now,
                }
            ),
            source="chat",
            life_id=active.life_id,
            principal_scope_hash=outer.principal_scope_hash,
            conversation_scope_hash=outer.conversation_scope_hash,
            request_id=outer.request_id,
            run_id=outer.run_id,
            generation=outer.generation,
            action_id=permission.action_id,
            action_version=permission.action_version,
            arguments_sha256=arguments_sha256,
            workspace_id=self.workspace_id,
            workspace_scope_hash=self.workspace_scope_hash,
            input_object_refs=tuple(item.object_id for item in grants),
            requested_side_effects=permission.allowed_side_effects,
            requested_resources=resources,
            source_refs=authorization_source_refs,
            payload_sha256=materialized_arguments_sha256,
            target_ref=target_ref,
            target_snapshot_sha256=target_snapshot_sha256,
            attachment_set_sha256=canonical_sha256(list(object_grant_values)),
            composition_execution_binding=binding,
            created_at_ms=now,
            expires_at_ms=expires_at_ms,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        knobs = derive_impact_knobs(
            permission.action_id,
            materialized.arguments,
            target=materialized.target,
            target_state=target_state,
            workspace_root=str(self.workspace_root),
            permission=permission,
        )
        # Exact explicit-schema validation eliminates the evaluator's generic
        # 100-milli "unknown arguments" baseline.  Any independently-derived
        # higher signal remains untouched and therefore still raises risk.
        uncertainty_milli = knobs["uncertainty_milli"]
        if uncertainty_milli == 100:
            uncertainty_milli = 0
        impact = compute_action_impact(
            intent,
            permission,
            affected_internal_nodes=("node_omni_body_runtime",),
            external_recipient_count=knobs["external_recipient_count"],
            credential_scope_milli=knobs["credential_scope_milli"],
            privacy_scope_milli=knobs["privacy_scope_milli"],
            blast_radius_milli=knobs["blast_radius_milli"],
            irreversibility_milli=knobs["irreversibility_milli"],
            uncertainty_milli=uncertainty_milli,
            target_snapshot_sha256=target_snapshot_sha256,
            created_at_ms=now,
        )
        policy_snapshot_sha256 = canonical_sha256(
            {
                "policy": "tiangong.composition-step.a0-read-only.v1",
                "registry_sha256": self.registry.registry_sha256,
                "schema_catalog_sha256": catalog.catalog_sha256,
            }
        )
        decision = PolicyEngine(
            self.registry,
            policy_snapshot_sha256=policy_snapshot_sha256,
            skill_catalog_hash=self.skill_catalog_hash,
            capability_manifest_hash=self.composition_capability_manifest_hash,
            component_manifest_hash=self.component_manifest_hash,
        ).evaluate(
            intent,
            impact,
            decided_at_ms=now,
            authorization_source_refs=authorization_source_refs,
            expected_composition_binding=binding,
        )
        if decision.outcome != "ALLOW" or decision.computed_risk != "A0":
            raise OmniGrantAuthorityError(
                "composition.authorization.policy_rejected", status=403
            )
        claim = EffectClaim(
            effect_id=effect_id,
            request_id=outer.request_id,
            run_id=outer.run_id,
            run_sequence=run_sequence,
            generation=outer.generation,
            effect_kind="execution",
            ordinal=ordinal,
            intent_sha256=effect_intent_sha256,
            pipeline_version=COMPOSITION_STEP_PIPELINE_VERSION,
            attempt=1,
            claim_revision=1,
            lease_epoch=self.gateway_epoch,
            supersedes_claim_sha256=None,
            owner_component_id="tiangong-backend",
            claimed_at_ms=now,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        child_payload = ExecutionTicketPayload(
            ticket_id="execution-ticket-" + canonical_sha256(
                {
                    "effect_id": effect_id,
                    "decision_sha256": decision.decision_sha256,
                }
            ),
            nonce="execution-nonce-" + canonical_sha256(
                {
                    "effect_id": effect_id,
                    "issued_at_ms": now,
                    "random": secrets.token_hex(16),
                }
            ),
            issued_at_ms=now,
            not_before_ms=now,
            expires_at_ms=expires_at_ms,
            gateway_epoch=self.gateway_epoch,
            fence_epoch=max(1, raw_fence_epoch),
            request_id=outer.request_id,
            run_id=outer.run_id,
            generation=outer.generation,
            effect_id=effect_id,
            channel=outer.channel,
            tenant_id=outer.tenant_id,
            link_account_id=outer.link_account_id,
            conversation_scope_hash=outer.conversation_scope_hash,
            principal_scope_hash=outer.principal_scope_hash,
            capability_manifest_hash=self.composition_capability_manifest_hash,
            policy_snapshot_hash=decision.policy_snapshot_sha256,
            policy_coverage_sha256=decision.policy_coverage_sha256,
            intent_id=intent.intent_id,
            intent_sha256=intent.intent_sha256,
            canonical_invocation_sha256=intent.canonical_invocation_sha256,
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            component_manifest_hash=self.component_manifest_hash,
            life_snapshot_revision=outer.life_snapshot_revision,
            life_snapshot_hash=outer.life_snapshot_hash,
            claim_sha256=claim.claim_sha256,
            claim_revision=claim.claim_revision,
            claim_lease_epoch=claim.lease_epoch,
            risk_class="A0",
            action_id=permission.action_id,
            action_version=permission.action_version,
            argument_schema_sha256=schema.argument_schema_sha256,
            arguments_hash=arguments_sha256,
            workspace_id=self.workspace_id,
            input_objects=grants,
            object_grants_sha256=canonical_sha256(list(object_grant_values)),
            output_root_id=outer.output_root_id,
            artifact_intent_id=outer.artifact_intent_id,
            max_output_bytes=resources.max_output_bytes,
            max_runtime_ms=resources.max_runtime_ms,
            max_tool_calls=resources.max_tool_calls,
            resource_envelope_sha256=resources.sha256(),
            allowed_side_effects=permission.allowed_side_effects,
            side_effect_envelope_sha256=canonical_sha256(
                {"allowed_side_effects": list(permission.allowed_side_effects)}
            ),
            composition_execution_binding=binding,
        )
        child_ticket = self.signer.sign_execution(child_payload)
        grant = issue_omni_capability_grant(
            signer=self.signer,
            ticket=child_ticket,
            intent=intent,
            permission=permission,
            decision=decision,
            nonce="omni-nonce-" + canonical_sha256(
                {
                    "ticket_id": child_payload.ticket_id,
                    "random": secrets.token_hex(16),
                }
            ),
            issued_at_ms=now,
            expires_at_ms=expires_at_ms,
        )
        binding_value = binding.model_dump(mode="json")
        runtime = {
            "execution_ticket_id": child_payload.ticket_id,
            "request_id": outer.request_id,
            "run_id": outer.run_id,
            "generation": outer.generation,
            "effect_id": effect_id,
            "step_id": step_id,
            "executable_plan_id": plan.executable_plan_id,
            "composition_binding_sha256": binding.binding_sha256,
            "composition_execution_binding": binding_value,
            "principal_scope_hash": outer.principal_scope_hash,
            "workspace_id": self.workspace_id,
            "action_id": permission.action_id,
            "action_version": permission.action_version,
            "decision_sha256": decision.decision_sha256,
            "impact_sha256": impact.impact_sha256,
            "action_permission_sha256": permission.permission_sha256,
            "action_registry_sha256": self.registry.registry_sha256,
            "capability_manifest_hash": self.composition_capability_manifest_hash,
            "component_manifest_hash": self.component_manifest_hash,
            "confirmation_sha256": None,
            "skill_id": None,
            "skill_version": None,
            "skill_sha256": None,
            "skill_activation_sha256": None,
            "gateway_url": self.gateway_url,
            "session_id": active.session_id,
            # This is the immutable authorization receipt.  The P7D runtime
            # coordinator derives a detached execution copy and forces that
            # copy to False so Gateway FactLedger remains the sole fact writer.
            "fact_kernel_enabled": True,
            "gateway_epoch": self.gateway_epoch,
            "trust_bundle_sha256": trust_bundle.bundle_sha256,
            "trust_bundle": trust_bundle.model_dump(mode="json"),
            "user_path_roots": [],
        }
        response = {
            "status": "OK",
            "grant": grant.model_dump(mode="json"),
            "runtime": runtime,
            "decision": {
                "decision_id": decision.decision_id,
                "decision_sha256": decision.decision_sha256,
                "risk_class": decision.computed_risk,
                "reason_codes": list(decision.reason_codes),
            },
        }
        artifacts = CompositionStepAuthorizationArtifacts.build(
            intent=intent,
            impact=impact,
            decision=decision,
            signed_ticket=child_ticket,
            signed_grant=grant,
            runtime_response=response,
        )
        try:
            persisted, _created = store.commit_composition_step_authorization(
                request,
                parent_ticket=active.ticket,
                artifacts=artifacts,
                now_ms=now,
            )
        except Exception as exc:
            raise OmniGrantAuthorityError(
                "composition.authorization.commit_failed", status=409
            ) from exc
        self._record_composition_evidence(persisted, permission, active)
        return persisted.runtime_response

    def issue(self, payload: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
        # A response can be lost after the first request reached this method.
        # Serialize one ticket/call_id so the retry observes the cached signed
        # response instead of racing a second grant mint.
        cache_key = (
            str(payload.get("ticket_id") or ""),
            str(payload.get("call_id") or ""),
        )
        with self._lock:
            call_lock = self._call_locks.setdefault(cache_key, threading.Lock())
        with call_lock:
            return self._issue_once(payload, now_ms=now_ms)

    def _issue_once(self, payload: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
        if set(payload) != {
            "ticket_id",
            "call_id",
            "request_id",
            "run_id",
            "generation",
            "principal_scope_hash",
            "action",
            "target",
            "args",
            "workspace",
        }:
            raise OmniGrantAuthorityError("omni.grant_request.fields_invalid")
        now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        active = self._get_active(payload, now)
        action = str(payload.get("action") or "").strip()
        call_id = str(payload.get("call_id") or "").strip()
        target = str(payload.get("target") or "").strip()
        args = payload.get("args")
        if (
            not action
            or not isinstance(args, Mapping)
            or not re.fullmatch(r"toolcall_[0-9a-f]{64}", call_id)
        ):
            raise OmniGrantAuthorityError("omni.grant_request.action_or_args_invalid")
        workspace = Path(str(payload.get("workspace") or "")).expanduser()
        try:
            workspace = workspace.resolve(strict=True)
        except OSError as exc:
            raise OmniGrantAuthorityError("omni.grant_request.workspace_invalid") from exc
        if workspace != self.workspace_root:
            raise OmniGrantAuthorityError("omni.grant_request.workspace_mismatch")
        permission = next((item for item in self.registry.permissions if item.action_id == action), None)
        if permission is None:
            raise OmniGrantAuthorityError("omni.action.not_registered", status=403)
        self._validate_no_authority_fields(args)
        user_roots = self._user_specified_roots(str(payload.get("request_id") or ""))
        self._validate_paths(target, args, allow_absolute=permission.allow_absolute_paths, user_roots=user_roots)
        if self._contains_destructive_overwrite(args) and "destructive" not in permission.allowed_side_effects:
            raise OmniGrantAuthorityError("omni.overwrite.not_authorized", status=403)

        arguments_sha256 = self._invocation_hash(action, target, args)
        cache_key = (active.ticket.payload.ticket_id, call_id)
        with self._lock:
            cached = self._issued.get(cache_key)
            if cached is not None:
                cached_arguments_sha256, cached_expires_at_ms, cached_response = cached
                if cached_arguments_sha256 != arguments_sha256:
                    raise OmniGrantAuthorityError("omni.call_id.conflict", status=409)
                if now <= cached_expires_at_ms:
                    return deepcopy(cached_response)
                self._issued.pop(cache_key, None)

        outer = active.ticket.payload
        # D-06 统一 admission：确定性子 effect 身份（同 call_id 永久幂等）。
        # 内存缓存只是快路径；台账首结果才是幂等权威 —— 缓存过期/网关重启后
        # 走到这里时 effect_id 不变，命中既有 admission 即重放首响应。
        run_sequence, sub_intent_sha256, effect_id = self._sub_effect_identity(
            outer, call_id=call_id, action=action, target=target,
            arguments_sha256=arguments_sha256,
        )
        existing_effect = self._effect_store.get_effect(effect_id)
        if existing_effect is not None:
            if existing_effect.result is None:
                raise OmniGrantAuthorityError("omni.effect.admission_incomplete", status=409)
            recorded_response = self._recorded_admission_response(effect_id)
            if recorded_response is None:
                raise OmniGrantAuthorityError("omni.effect.receipt_missing", status=409)
            return recorded_response

        try:
            trust_bundle = self.trust_bundle_provider(now)
        except Exception as exc:
            raise OmniGrantAuthorityError("omni.trust_bundle.unavailable", status=503) from exc
        if (
            trust_bundle.production_ready is not True
            or trust_bundle.gateway_epoch != self.gateway_epoch
            or not trust_bundle.has_valid_sha256()
        ):
            raise OmniGrantAuthorityError("omni.trust_bundle.invalid", status=503)

        resources = ResourceEnvelope(
            max_runtime_ms=min(outer.max_runtime_ms, 3_600_000),
            max_output_bytes=outer.max_output_bytes,
            max_tool_calls=max(1, min(outer.max_tool_calls, 10_000)),
        )
        intent_seed = {
            "ticket_id": outer.ticket_id,
            "call_id": call_id,
            "action": action,
            "arguments_sha256": arguments_sha256,
            "issued_at_ms": now,
            "nonce": secrets.token_hex(16),
        }
        # D-08: bind the authorization provenance of this intent.  The only
        # authorizing facts are the current user instruction carried by the
        # signed outer ticket and the Life evidence of this run; the model's
        # proposed action/target/args are data, never authorization sources.
        authorization_source_refs = tuple(
            sorted(
                (
                    SourceRef(
                        source_type="CURRENT_USER_INSTRUCTION",
                        object_id=outer.ticket_id,
                        object_revision=1,
                        sha256=canonical_sha256(active.ticket.payload.model_dump(mode="json")),
                    ),
                    SourceRef(
                        source_type="PREAUTHORIZED_USER_FACT",
                        object_id=active.life_evidence_ref,
                        object_revision=1,
                        sha256=active.life_evidence_ref[4:],
                    ),
                ),
                key=lambda ref: ref.sort_key(),
            )
        )
        # D-09: derive the impact knobs from the normalized arguments, the
        # actual target and the evaluation-time target state instead of
        # passing constants.  Derivation is deterministic and can only raise
        # the machine floors, never lower them.
        target_state = probe_target_state(target, self.workspace_root)
        target_snapshot_sha256 = canonical_sha256(target_state) if target_state else None
        target_ref = "target-" + canonical_sha256({"action": action, "target": target}) if target else None
        knobs = derive_impact_knobs(
            action,
            args,
            target=target,
            target_state=target_state,
            workspace_root=str(self.workspace_root),
            permission=permission,
        )
        intent = ActionIntent(
            intent_id="intent-" + canonical_sha256(intent_seed),
            source="chat",
            life_id=active.life_id,
            principal_scope_hash=outer.principal_scope_hash,
            conversation_scope_hash=outer.conversation_scope_hash,
            request_id=outer.request_id,
            run_id=outer.run_id,
            generation=outer.generation,
            action_id=action,
            action_version=permission.action_version,
            arguments_sha256=arguments_sha256,
            workspace_id=self.workspace_id,
            workspace_scope_hash=self.workspace_scope_hash,
            input_object_refs=tuple(sorted(item.object_id for item in outer.input_objects)),
            requested_side_effects=permission.allowed_side_effects,
            requested_resources=resources,
            source_refs=authorization_source_refs,
            payload_sha256=arguments_sha256,
            target_ref=target_ref,
            target_snapshot_sha256=target_snapshot_sha256,
            attachment_set_sha256=canonical_sha256(
                [
                    {"object_id": item.object_id, "revision": item.revision, "sha256": item.sha256}
                    for item in outer.input_objects
                ]
            ),
            created_at_ms=now,
            expires_at_ms=now + 60_000,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        impact = compute_action_impact(
            intent,
            permission,
            affected_internal_nodes=("node_omni_body_runtime",),
            external_recipient_count=knobs["external_recipient_count"],
            credential_scope_milli=knobs["credential_scope_milli"],
            privacy_scope_milli=knobs["privacy_scope_milli"],
            blast_radius_milli=knobs["blast_radius_milli"],
            irreversibility_milli=knobs["irreversibility_milli"],
            uncertainty_milli=knobs["uncertainty_milli"],
            target_snapshot_sha256=target_snapshot_sha256,
            created_at_ms=now,
        )
        policy_snapshot_sha256 = canonical_sha256(
            {
                "policy": "tiangong.omni.autonomous-a0-a4.a5-deny.v3",
                "registry_sha256": self.registry.registry_sha256,
            }
        )
        decision = PolicyEngine(
            self.registry,
            policy_snapshot_sha256=policy_snapshot_sha256,
            skill_catalog_hash=self.skill_catalog_hash,
            capability_manifest_hash=self.capability_manifest_hash,
            component_manifest_hash=self.component_manifest_hash,
        ).evaluate(
            intent,
            impact,
            decided_at_ms=now,
            authorization_source_refs=authorization_source_refs,
        )
        if decision.outcome != "ALLOW":
            self.evidence.record_evaluation(
                intent=intent,
                impact=impact,
                permission=permission,
                registry=self.registry,
                decision=decision,
                ticket=None,
                grant=None,
                observed_at_ms=now,
            )
            raise OmniGrantAuthorityError("omni.policy.rejected", status=403)

        # D-06：claim-before-ticket —— 先构造确定性 claim（含 revision/lease），
        # 再把 claim 绑定进子票；台账 admission 与出票结果同一事务提交。
        claim = EffectClaim(
            effect_id=effect_id,
            request_id=outer.request_id,
            run_id=outer.run_id,
            run_sequence=run_sequence,
            generation=outer.generation,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=sub_intent_sha256,
            pipeline_version=_OMNI_SUB_EFFECT_PIPELINE_VERSION,
            attempt=1,
            claim_revision=1,
            lease_epoch=self.gateway_epoch,
            supersedes_claim_sha256=None,
            owner_component_id="tiangong-backend",
            claimed_at_ms=now,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        fence_status = self._effect_store.action_fence_status()
        raw_fence_epoch = int(fence_status["action_fence_epoch"])
        # 合同 fence_epoch >= 1；store 创世 epoch 为 0（从未 fence）。
        # 票面取 max(1, raw)，与父级票的合同默认值一致；台账 CAS 仍按 raw epoch。
        fence_epoch = max(1, raw_fence_epoch)
        child_payload = ExecutionTicketPayload(
            ticket_id="execution-ticket-" + canonical_sha256(
                {"effect_id": effect_id, "decision": decision.decision_sha256}
            ),
            nonce="execution-nonce-" + canonical_sha256(
                {"effect_id": effect_id, "issued_at_ms": now, "random": secrets.token_hex(16)}
            ),
            issued_at_ms=now,
            not_before_ms=now,
            expires_at_ms=now + 60_000,
            gateway_epoch=self.gateway_epoch,
            fence_epoch=fence_epoch,
            request_id=outer.request_id,
            run_id=outer.run_id,
            generation=outer.generation,
            effect_id=effect_id,
            channel=outer.channel,
            tenant_id=outer.tenant_id,
            link_account_id=outer.link_account_id,
            conversation_scope_hash=outer.conversation_scope_hash,
            principal_scope_hash=outer.principal_scope_hash,
            capability_manifest_hash=self.capability_manifest_hash,
            policy_snapshot_hash=decision.policy_snapshot_sha256,
            policy_coverage_sha256=decision.policy_coverage_sha256,
            intent_id=intent.intent_id,
            intent_sha256=intent.intent_sha256,
            canonical_invocation_sha256=intent.canonical_invocation_sha256,
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            component_manifest_hash=self.component_manifest_hash,
            life_snapshot_revision=outer.life_snapshot_revision,
            life_snapshot_hash=outer.life_snapshot_hash,
            claim_sha256=claim.claim_sha256,
            claim_revision=claim.claim_revision,
            claim_lease_epoch=claim.lease_epoch,
            risk_class=decision.computed_risk,
            action_id=action,
            action_version=permission.action_version,
            argument_schema_sha256=canonical_sha256(
                {"schema": "tiangong.omni.invocation.v1", "fields": ["action", "target", "args"]}
            ),
            arguments_hash=arguments_sha256,
            workspace_id=self.workspace_id,
            input_objects=outer.input_objects,
            object_grants_sha256=outer.object_grants_sha256,
            output_root_id=outer.output_root_id,
            artifact_intent_id=outer.artifact_intent_id,
            max_output_bytes=resources.max_output_bytes,
            max_runtime_ms=resources.max_runtime_ms,
            max_tool_calls=resources.max_tool_calls,
            resource_envelope_sha256=resources.sha256(),
            allowed_side_effects=permission.allowed_side_effects,
            side_effect_envelope_sha256=canonical_sha256(
                {"allowed_side_effects": list(permission.allowed_side_effects)}
            ),
        )
        child_ticket = self.signer.sign_execution(child_payload)
        grant = issue_omni_capability_grant(
            signer=self.signer,
            ticket=child_ticket,
            intent=intent,
            permission=permission,
            decision=decision,
            nonce="omni-nonce-" + canonical_sha256(
                {"ticket_id": child_payload.ticket_id, "random": secrets.token_hex(16)}
            ),
            issued_at_ms=now,
            expires_at_ms=now + 60_000,
        )
        # vNext 绑定（ticket_sha256/四元组/scope）已在签发处一次成形——
        # 同一逻辑 grant 只允许一次 capability 签名，不做补齐重签。
        self.evidence.record_evaluation(
            intent=intent,
            impact=impact,
            permission=permission,
            registry=self.registry,
            decision=decision,
            ticket=child_ticket,
            grant=grant,
            observed_at_ms=now,
        )
        runtime = {
            "execution_ticket_id": child_payload.ticket_id,
            "request_id": outer.request_id,
            "run_id": outer.run_id,
            "generation": outer.generation,
            "principal_scope_hash": outer.principal_scope_hash,
            "workspace_id": self.workspace_id,
            "action_version": permission.action_version,
            "decision_sha256": decision.decision_sha256,
            "impact_sha256": impact.impact_sha256,
            "action_permission_sha256": permission.permission_sha256,
            "action_registry_sha256": self.registry.registry_sha256,
            "capability_manifest_hash": self.capability_manifest_hash,
            "component_manifest_hash": self.component_manifest_hash,
            "confirmation_sha256": None,
            "skill_id": None,
            "skill_version": None,
            "skill_sha256": None,
            "skill_activation_sha256": None,
            "gateway_url": self.gateway_url,
            "session_id": active.session_id,
            "fact_kernel_enabled": True,
            "gateway_epoch": self.gateway_epoch,
            "trust_bundle_sha256": trust_bundle.bundle_sha256,
            "trust_bundle": trust_bundle.model_dump(mode="json"),
            # D-21：网关从用户本轮原文确定性提取的指定路径根，随签发结果下发——
            # 运行时/contracts 层据此放行（不自行提取、不认模型自报）。
            "user_path_roots": [str(root) for root in user_roots],
        }
        response = {
            "status": "OK",
            "grant": grant.model_dump(mode="json"),
            "runtime": runtime,
            "decision": {
                "decision_id": decision.decision_id,
                "decision_sha256": decision.decision_sha256,
                "risk_class": decision.computed_risk,
                "reason_codes": list(decision.reason_codes),
            },
        }
        # D-06 统一 admission：claim + STARTED + 双 nonce 落库 + 首响应 RECEIPT
        # 单事务提交（无崩溃窗口）；并发/重试同 call_id 时首个提交获胜，
        # 其余拿回台账记录的首响应（自己刚铸的 grant 被丢弃，绝不双发）。
        child_ticket_sha256 = canonical_sha256(child_payload.model_dump(mode="json"))
        grant_payload_sha256 = canonical_sha256(grant.payload.model_dump(mode="json"))
        admission_result = EffectResult(
            result_id="effect_result_omni_" + effect_id[4:20],
            effect_id=effect_id,
            status="SUCCEEDED",
            fact_id="fact_omni_admission_" + effect_id[4:20],
            evidence_sha256=canonical_sha256(
                {
                    "domain": "tiangong.omni.sub-effect-admission.v1",
                    "effect_id": effect_id,
                    "ticket_sha256": child_ticket_sha256,
                    "grant_sha256": grant_payload_sha256,
                }
            ),
            observed_at_ms=now,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        try:
            _, created, recorded_response = self._effect_store.admit_sub_effect(
                claim=claim,
                result=admission_result,
                started_at_ms=now,
                receipt_response=response,
                nonces=(
                    {
                        "issuer": "tiangong-total-gateway",
                        "audience": "tiangong-backend",
                        "purpose": "execution_ticket",
                        "nonce": child_payload.nonce,
                        "payload_sha256": child_ticket_sha256,
                        "gateway_epoch": self.gateway_epoch,
                        "consumer_instance_id": _OMNI_NONCE_CONSUMER_ID,
                        "consumed_at_ms": now,
                        "expires_at_ms": child_payload.expires_at_ms,
                    },
                    {
                        "issuer": "tiangong-total-gateway",
                        "audience": "tiangong-backend",
                        "purpose": "omni_capability_grant",
                        "nonce": grant.payload.nonce,
                        "payload_sha256": grant_payload_sha256,
                        "gateway_epoch": self.gateway_epoch,
                        "consumer_instance_id": _OMNI_NONCE_CONSUMER_ID,
                        "consumed_at_ms": now,
                        "expires_at_ms": grant.payload.expires_at_ms,
                    },
                ),
                ticket_id=child_payload.ticket_id,
                ticket_sha256=child_ticket_sha256,
                grant_sha256=grant_payload_sha256,
                nonce_sha256=canonical_sha256(
                    {"child_nonce": child_payload.nonce, "grant_nonce": grant.payload.nonce}
                ),
                expected_fence_epoch=raw_fence_epoch,
            )
        except StoreCasConflict as exc:
            raise OmniGrantAuthorityError("omni.action_fence.advanced", status=409) from exc
        except StoreConflictError as exc:
            raise OmniGrantAuthorityError("omni.effect.admission_conflict", status=409) from exc
        if not created:
            if recorded_response is None:
                raise OmniGrantAuthorityError("omni.effect.receipt_missing", status=409)
            return deepcopy(recorded_response)
        with self._lock:
            existing = self._issued.get(cache_key)
            if existing is not None and existing[0] != arguments_sha256:
                raise OmniGrantAuthorityError("omni.call_id.conflict", status=409)
            self._issued[cache_key] = (
                arguments_sha256,
                now + 60_000,
                deepcopy(response),
            )
        return response


__all__ = [
    "ActiveExecutionAuthority",
    "OmniGrantAuthority",
    "OmniGrantAuthorityError",
]
