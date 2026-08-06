"""Gateway-owned deterministic ActionImpact evaluation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from contracts import (
    ActionImpact,
    ActionIntent,
    ActionPermission,
    ViabilityDelta,
    canonical_sha256,
)


_RISK_MILLI = {"A0": 0, "A1": 100, "A2": 300, "A3": 500, "A4": 700}

# --- D-09 derivation signals -------------------------------------------------
# The derivation below is a pure, deterministic function of its inputs.  Every
# rule can only *raise* a milli floor, never lower one; callers merge the
# result into ``compute_action_impact`` whose own floors still apply.
_CREDENTIAL_DIR_NAMES = frozenset({".ssh", ".aws", ".gnupg"})
_CREDENTIAL_FILE_NAMES = frozenset({".env"})
_CREDENTIAL_CONTENT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
)
_SEND_ACTION_RE = re.compile(
    r"(?:^|\.)(?:send|publish|upload|forward|share|post)(?:\.|$)", re.IGNORECASE
)
_DELETE_ACTION_RE = re.compile(
    r"(?:^|\.)(?:delete|delete_to_trash|remove|trash|wipe|purge|format)(?:\.|$)",
    re.IGNORECASE,
)
_TARGETED_ACTION_PREFIXES = (
    "file.",
    "code.",
    "docx.",
    "pptx.",
    "sheet.",
    "image.",
    "video.",
    "audio.",
    "zip.",
)
_RECIPIENT_KEYS = frozenset(
    {"to", "cc", "bcc", "recipient", "recipients", "email", "emails", "address", "addresses"}
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_PATH_HINT_RE = re.compile(r"(?:^[a-zA-Z]:[\\/])|(?:^\\\\)|(?:^~[\\/])|(?:^/)")
_DRIVE_ROOT_RE = re.compile(r"^[a-zA-Z]:[\\/]*$")
_MAX_WALK_DEPTH = 6
_MAX_WALK_STRINGS = 512


def _looks_like_path(text: str) -> bool:
    return bool(_PATH_HINT_RE.search(text.strip()))


def _path_hits_credential_zone(text: str) -> bool:
    normalized = text.strip().replace("/", "\\").casefold()
    parts = [part for part in normalized.split("\\") if part not in {"", ".", ".."}]
    if any(part in _CREDENTIAL_DIR_NAMES for part in parts):
        return True
    return bool(parts and parts[-1] in _CREDENTIAL_FILE_NAMES)


def _path_is_host_root(text: str) -> bool:
    stripped = text.strip()
    if _DRIVE_ROOT_RE.match(stripped):
        return True
    try:
        home = str(Path.home().resolve(strict=False)).replace("/", "\\").rstrip("\\").casefold()
    except Exception:
        return False
    candidate = stripped.replace("/", "\\").rstrip("\\").casefold()
    return bool(home) and candidate == home


def _path_outside_workspace(text: str, workspace_root: str) -> bool:
    if str(os.environ.get("TIANGONG_WORKSPACE_MODE") or "").strip().lower() == "full":
        # 全盘写入模式：任意用户可写位置不再视为“工作区外”提高 blast 影响。
        return False
    if not workspace_root or not _looks_like_path(text):
        return False
    try:
        candidate = os.path.normcase(str(Path(text.strip()).resolve(strict=False)))
        root = os.path.normcase(str(Path(workspace_root).resolve(strict=False)))
    except (OSError, ValueError):
        return False
    try:
        return os.path.commonpath([candidate, root]) != root
    except ValueError:
        # Different drives (or otherwise unrelatable roots) are outside by
        # definition; derivation only ever raises floors, never lowers them.
        return True


def _walk_strings(value: Any, *, _depth: int = 0, _bucket: list | None = None) -> list[tuple[str, str]]:
    """Collect ``(key, string)`` pairs from nested args, bounded and deterministic."""
    bucket = _bucket if _bucket is not None else []
    if len(bucket) >= _MAX_WALK_STRINGS or _depth > _MAX_WALK_DEPTH:
        return bucket
    if isinstance(value, str):
        bucket.append(("", value))
    elif isinstance(value, Mapping):
        for key in sorted(value, key=str):
            nested = value[key]
            if isinstance(nested, str):
                if len(bucket) < _MAX_WALK_STRINGS:
                    bucket.append((str(key).casefold(), nested))
            else:
                _walk_strings(nested, _depth=_depth + 1, _bucket=bucket)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_strings(item, _depth=_depth + 1, _bucket=bucket)
    return bucket


def probe_target_state(target: str, workspace_root: str | Path | None = None) -> dict[str, Any] | None:
    """Snapshot the evaluation-time state of a filesystem target (TOCTOU anchor).

    Returns ``None`` for non-filesystem targets (URLs, opaque resource ids that
    cannot be resolved); otherwise a mapping with ``exists``/``is_dir`` and, for
    existing targets, ``size_bytes``.  The probe is deterministic given the
    filesystem at evaluation time, which is exactly when the policy decision is
    bound to it.
    """
    text = str(target or "").strip()
    if not text or _URL_RE.search(text):
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            if workspace_root is None:
                return None
            path = Path(workspace_root) / path
        path = path.resolve(strict=False)
    except (OSError, ValueError):
        return None
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False, "is_dir": False}
    return {"exists": True, "is_dir": path.is_dir(), "size_bytes": int(stat.st_size)}


def derive_impact_knobs(
    action_id: str,
    args: Mapping[str, Any] | None = None,
    *,
    target: str = "",
    target_state: Mapping[str, Any] | None = None,
    workspace_root: str = "",
    permission: ActionPermission | None = None,
    scan_args: bool = True,
    external_content_count: int = 0,
) -> dict[str, int]:
    """Derive impact milli floors from normalized args, target and target state.

    D-09: the three production call sites used to pass constants, leaving the
    credential/privacy/blast/irreversibility knobs idle.  This derivation is
    deterministic (same inputs -> same outputs) and monotone: every rule only
    raises a floor, so the machine registry floor can never be diluted by
    caller-provided optimism.
    """
    action = str(action_id or "").strip().casefold()
    credential = 0
    privacy = 0
    blast = 0
    irreversible = 0
    uncertainty = 100

    pairs: list[tuple[str, str]] = []
    if scan_args and isinstance(args, Mapping):
        pairs = _walk_strings(args)
    target_text = str(target or "").strip()
    values = [text for _, text in pairs] + ([target_text] if target_text else [])

    for text in values:
        if any(pattern.search(text) for pattern in _CREDENTIAL_CONTENT_PATTERNS):
            credential = 900
            privacy = max(privacy, 700)
        if _looks_like_path(text) and _path_hits_credential_zone(text):
            credential = 900
            privacy = max(privacy, 700)
        if _path_outside_workspace(text, workspace_root):
            blast = max(blast, 700)
        if _path_is_host_root(text):
            blast = max(blast, 700)

    recipient_hits: set[str] = set()
    for key, text in pairs:
        if key in _RECIPIENT_KEYS:
            recipient_hits.update(match.lower() for match in _EMAIL_RE.findall(text))
    send_like = bool(_SEND_ACTION_RE.search(action))
    if send_like:
        for text in values:
            if _URL_RE.search(text):
                recipient_hits.add("<url-endpoint>")
                break
        if recipient_hits or any(_EMAIL_RE.search(text) for text in values):
            privacy = max(privacy, 500)
    if recipient_hits:
        blast = max(blast, 700)
        privacy = max(privacy, 500)

    delete_like = bool(_DELETE_ACTION_RE.search(action))
    if delete_like:
        irreversible = max(irreversible, 700)
    state = target_state if isinstance(target_state, Mapping) else None
    if state and state.get("exists"):
        if delete_like:
            if state.get("is_dir"):
                blast = max(blast, 900)
            irreversible = max(irreversible, 900)
        elif permission is not None and permission.effect in {"write", "update"}:
            irreversible = max(irreversible, 300)
    if delete_like and any(_path_is_host_root(text) for text in values):
        blast = max(blast, 900)
        irreversible = max(irreversible, 900)

    if action.startswith(_TARGETED_ACTION_PREFIXES) and not target_text:
        uncertainty = max(uncertainty, 300)
    if external_content_count > 0:
        uncertainty = max(uncertainty, 200)

    return {
        "credential_scope_milli": credential,
        "privacy_scope_milli": privacy,
        "blast_radius_milli": blast,
        "irreversibility_milli": irreversible,
        "uncertainty_milli": uncertainty,
        "external_recipient_count": min(len(recipient_hits), 1_000_000),
    }


def _milli(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
        raise ValueError(f"{name} must be an integer milli value")
    return value


_LIFE_EVENT_REF_RE = re.compile(r"lev_[0-9a-f]{64}")


def _risk_band(critical: int) -> str:
    if critical == 0:
        return "A0"
    if critical <= 200:
        return "A1"
    if critical <= 400:
        return "A2"
    if critical <= 600:
        return "A3"
    if critical <= 800:
        return "A4"
    return "A5"


def _resource_cost(intent: ActionIntent) -> int:
    resources = intent.requested_resources
    return max(
        min(1000, resources.max_runtime_ms * 1000 // 3_600_000),
        min(1000, resources.max_output_bytes * 1000 // 2_147_483_648),
        min(1000, resources.max_tool_calls * 1000 // 10_000),
    )


def compute_action_impact(
    intent: ActionIntent,
    permission: ActionPermission,
    *,
    affected_internal_nodes: Iterable[str] = (),
    touches_identity: bool = False,
    touches_soul: bool = False,
    touches_memory_keys: bool = False,
    touches_policy: bool = False,
    touches_core_code: bool = False,
    external_recipient_count: int = 0,
    credential_scope_milli: int = 0,
    privacy_scope_milli: int = 0,
    blast_radius_milli: int = 0,
    irreversibility_milli: int = 0,
    uncertainty_milli: int = 0,
    rollback_proof_ref: str | None = None,
    predicted_viability_deltas: Iterable[ViabilityDelta] = (),
    target_snapshot_sha256: str | None = None,
    created_at_ms: int,
) -> ActionImpact:
    """Recompute impact floors; caller values can only increase risk, never lower it."""

    if not intent.has_valid_sha256() or not permission.has_valid_sha256():
        raise ValueError("impact input digest is invalid")
    if intent.action_id != permission.action_id or intent.action_version != permission.action_version:
        raise ValueError("impact action binding is invalid")
    if created_at_ms < intent.created_at_ms or created_at_ms > intent.expires_at_ms:
        raise ValueError("impact evaluation is outside the intent lifetime")
    if (
        isinstance(external_recipient_count, bool)
        or not isinstance(external_recipient_count, int)
        or not 0 <= external_recipient_count <= 1_000_000
    ):
        raise ValueError("external recipient count is invalid")
    if target_snapshot_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", target_snapshot_sha256) is None:
        raise ValueError("target snapshot digest is invalid")
    nodes = tuple(sorted(set(str(value) for value in affected_internal_nodes)))
    if any(not value or len(value) > 160 for value in nodes):
        raise ValueError("affected internal node identity is invalid")
    # The intent's vNext provenance set may carry several source types; the
    # impact must remain anchored to at least one persisted LifeEvent evidence
    # ref, and only those refs are copied into the impact's event trail.
    life_event_refs = tuple(
        sorted(
            {
                value
                for value in intent.source_evidence_refs
                if _LIFE_EVENT_REF_RE.fullmatch(value) is not None
            }
        )
    )
    if not life_event_refs:
        raise ValueError("ActionImpact requires persisted LifeEvent evidence")

    credential = _milli(credential_scope_milli, "credential scope")
    privacy = _milli(privacy_scope_milli, "privacy scope")
    blast = _milli(blast_radius_milli, "blast radius")
    irreversible = _milli(irreversibility_milli, "irreversibility")
    uncertainty = _milli(uncertainty_milli, "uncertainty")
    floor = _RISK_MILLI[permission.effective_risk]
    workspace = 0
    if "local_write" in permission.allowed_side_effects:
        workspace = max(workspace, 300)
    if permission.effective_risk == "A3":
        workspace = max(workspace, 500)
    if permission.effective_risk == "A4":
        blast = max(blast, floor)
    if "external_write" in permission.allowed_side_effects or "external_send" in permission.allowed_side_effects:
        blast = max(blast, 700)
        privacy = max(privacy, 500)
    if "destructive" in permission.allowed_side_effects:
        irreversible = max(irreversible, 700)
    if external_recipient_count:
        blast = max(blast, 700)
        privacy = max(privacy, 500)

    folded_nodes = " ".join(nodes).casefold()
    touches_identity = touches_identity or "identity" in folded_nodes
    touches_soul = touches_soul or "soul" in folded_nodes
    touches_memory_keys = touches_memory_keys or "memory_key" in folded_nodes
    touches_policy = touches_policy or "policy" in folded_nodes
    touches_core_code = touches_core_code or "core_code" in folded_nodes
    if touches_identity or touches_soul or touches_memory_keys or touches_policy:
        blast = 1000
        irreversible = 1000
    if touches_core_code:
        blast = max(blast, 900)
        uncertainty = max(uncertainty, 900)
    if credential:
        credential = max(credential, 900)
    if irreversible >= 800:
        rollback_proof_ref = None

    deltas = tuple(sorted(predicted_viability_deltas, key=lambda item: item.dimension))
    # Honest vNext binding: the impact carries the intent digest it was
    # computed from, its own dynamic risk band (identical inputs to
    # risk_from_action_impact), and the evaluation-time target-state snapshot.
    critical = max(workspace, credential, privacy, blast, irreversible, uncertainty)
    if external_recipient_count:
        critical = max(critical, 700)
    if touches_core_code or credential:
        critical = max(critical, 900)
    if touches_identity or touches_soul or touches_memory_keys or touches_policy:
        critical = 1000
    unsigned = ActionImpact(
        impact_id="impact-" + canonical_sha256(
            {
                "intent_sha256": intent.intent_sha256,
                "permission_sha256": permission.permission_sha256,
                "created_at_ms": created_at_ms,
            }
        ),
        life_id=intent.life_id,
        action_id=intent.action_id,
        intent_sha256=intent.intent_sha256,
        dynamic_risk=_risk_band(critical),
        target_snapshot_sha256=target_snapshot_sha256,
        affected_internal_nodes=nodes,
        touches_identity=touches_identity,
        touches_soul=touches_soul,
        touches_memory_keys=touches_memory_keys,
        touches_policy=touches_policy,
        touches_core_code=touches_core_code,
        workspace_scope_milli=workspace,
        external_recipient_count=external_recipient_count,
        credential_scope_milli=credential,
        privacy_scope_milli=privacy,
        blast_radius_milli=blast,
        irreversibility_milli=irreversible,
        uncertainty_milli=uncertainty,
        rollback_proof_ref=rollback_proof_ref,
        estimated_resource_cost_milli=_resource_cost(intent),
        predicted_viability_deltas=deltas,
        source_event_ids=life_event_refs,
        created_at_ms=created_at_ms,
        impact_sha256="0" * 64,
    )
    return unsigned.with_computed_impact_sha256()


def risk_from_action_impact(impact: ActionImpact) -> str:
    if not impact.has_valid_impact_sha256():
        raise ValueError("action impact digest is invalid")
    critical = max(
        impact.workspace_scope_milli,
        impact.credential_scope_milli,
        impact.privacy_scope_milli,
        impact.blast_radius_milli,
        impact.irreversibility_milli,
        impact.uncertainty_milli,
    )
    if impact.external_recipient_count:
        critical = max(critical, 700)
    if impact.touches_core_code or impact.credential_scope_milli:
        critical = max(critical, 900)
    if impact.touches_identity or impact.touches_soul or impact.touches_memory_keys or impact.touches_policy:
        critical = 1000
    return _risk_band(critical)


__all__ = [
    "compute_action_impact",
    "derive_impact_knobs",
    "probe_target_state",
    "risk_from_action_impact",
]
