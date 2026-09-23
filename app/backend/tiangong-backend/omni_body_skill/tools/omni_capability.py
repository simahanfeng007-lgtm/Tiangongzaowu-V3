"""Consumer-side verification for Gateway-signed Omni capability grants."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping


# Independent leaf consumer of the fixed profile wire protocol.  Keep these
# definitions identical to contracts/composition_profile.py; the parity test
# detects drift without making the Body import its issuer or Gateway siblings.
# This verifier is also loaded by exact file path without a package context.
WORKSPACE_WRITE_PROFILE_ID = "composition.workspace-write.v1"


_WRITE_ACTIONS = {
    "file.write": ("A3", "write"),
    "code.write": ("A3", "write"),
    "code.patch_replace": ("A3", "write"),
    "file.mkdir": ("A2", "write"),
}


WORKSPACE_WRITE_ACTIONS = frozenset(_WRITE_ACTIONS)


WORKSPACE_READ_ACTIONS = frozenset({"file.read", "file.list", "file.hash"})


_PROFILE_DOCUMENT = {
    "id": WORKSPACE_WRITE_PROFILE_ID,
    "read_actions": sorted(WORKSPACE_READ_ACTIONS),
    "write_actions": {key: list(value) for key, value in sorted(_WRITE_ACTIONS.items())},
    "path_policy": "workspace_only",
    "allow_shell": False,
    "allow_python": False,
    "max_utf8_bytes": 1048576,
    "patch": "literal-positive-count-no-noop",
    "code_write": "syntax_check_false",
    "target_snapshot": "native-path-and-content-sha256",
}


WORKSPACE_WRITE_PROFILE_SHA256 = hashlib.sha256(json.dumps(
    _PROFILE_DOCUMENT, ensure_ascii=False, sort_keys=True,
    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


WORKSPACE_PYTHON_PROFILE_ID = "composition.workspace-python.v1"


WORKSPACE_PYTHON_PROFILE_SHA256 = hashlib.sha256(json.dumps({
    "id": WORKSPACE_PYTHON_PROFILE_ID, "write_profile_sha256": WORKSPACE_WRITE_PROFILE_SHA256,
    "execution_action": "python.run", "target": "existing-workspace-py-script",
    "argv": "bounded-string-list", "allow_inline_code": False,
    "containment": "windows-appcontainer", "require_os_containment": True,
    "network": "denied", "environment": "allowlist-no-parent-secrets",
    "timeout_seconds": 60, "max_output_bytes": 4194304, "max_changed_bytes": 4194304,
    "memory_limit_bytes": 2147483648, "process_limit": 32,
    "commit": "success-only-workspace-create-update-no-delete",
}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def composition_profile_valid(profile_id=None, profile_sha256=None) -> bool:
    return (profile_id, profile_sha256) in {
        (None, None), (WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256),
        (WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256)}


def composition_profile_fields(value) -> dict[str, Any]:
    """Extract only sealed contract fields, never infer them from arguments."""
    get = value.get if isinstance(value, Mapping) else lambda key: getattr(value, key, None)
    return {"profile_id": get("execution_profile_id"),
            "profile_sha256": get("execution_profile_sha256")}


def composition_authority_allowed(*, action_id, risk_class, allowed_side_effects,
                                  allow_shell=False, allow_python=False,
                                  profile_id=None, profile_sha256=None) -> bool:
    if (not composition_profile_valid(profile_id, profile_sha256)
            or allow_shell
            or not isinstance(allowed_side_effects, (tuple, list, set, frozenset))):
        return False
    effects = set(allowed_side_effects)
    if profile_id == WORKSPACE_PYTHON_PROFILE_ID and action_id == "python.run":
        return risk_class == "A4" and allow_python is True and effects == {"local_write", "read"}
    if allow_python:
        return False
    if profile_id is None:
        return risk_class == "A0" and effects.issubset({"none", "read"})
    if action_id in WORKSPACE_READ_ACTIONS:
        return risk_class == "A0" and effects == {"read"}
    expected = _WRITE_ACTIONS.get(action_id)
    return bool(expected and risk_class == expected[0] and effects == {"local_write", "read"})


def validate_composition_arguments(action_id, arguments, *, profile_id=None, profile_sha256=None) -> None:
    """Additional fixed-profile restrictions after the actual action schema."""
    if not composition_profile_valid(profile_id, profile_sha256):
        raise ValueError("unknown composition execution profile")
    if profile_id is None or action_id in WORKSPACE_READ_ACTIONS:
        return
    if profile_id == WORKSPACE_PYTHON_PROFILE_ID and action_id == "python.run":
        if (not isinstance(arguments, Mapping) or set(arguments) - {"argv", "timeout"}
                or type(arguments.get("timeout", 30)) is not int
                or not 1 <= arguments.get("timeout", 30) <= 60
                or type(arguments.get("argv", [])) is not list
                or len(arguments.get("argv", [])) > 64
                or any(type(item) is not str or len(item.encode("utf-8")) > 4096 or "\x00" in item
                       for item in arguments.get("argv", []))):
            raise ValueError("controlled Python requires bounded argv and timeout; inline code is not allowed")
        return
    if action_id not in WORKSPACE_WRITE_ACTIONS or not isinstance(arguments, Mapping):
        raise ValueError("action is outside the composition execution profile")
    allowed = {
        "file.write": {"content", "encoding", "binary"},
        "code.write": {"content", "encoding", "binary", "syntax_check", "language"},
        "code.patch_replace": {"find", "replace", "count", "regex", "allow_noop", "encoding"},
        "file.mkdir": {"exist_ok"},
    }[action_id]
    if set(arguments) - allowed:
        raise ValueError("unsupported controlled write argument")
    if action_id in {"file.write", "code.write"}:
        content = arguments.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1048576:
            raise ValueError("controlled write requires bounded UTF-8 text")
        if arguments.get("binary", False) is not False:
            raise ValueError("controlled write does not allow binary encoding")
    if arguments.get("encoding", "utf-8").lower().replace("_", "-") not in {"utf-8", "utf8"}:
        raise ValueError("controlled write requires UTF-8")
    if action_id == "code.write" and arguments.get("syntax_check") is not False:
        raise ValueError("controlled code.write requires syntax_check=false; execution is separate")
    if action_id == "code.patch_replace":
        if (not isinstance(arguments.get("find"), str) or not arguments["find"]
                or not isinstance(arguments.get("replace", ""), str)
                or type(arguments.get("count")) is not int or not 1 <= arguments["count"] <= 10000
                or arguments.get("regex", False) is not False
                or arguments.get("allow_noop", False) is not False):
            raise ValueError("controlled patch requires a bounded literal replacement")
        if sum(len(arguments.get(key, "").encode("utf-8")) for key in ("find", "replace")) > 1048576:
            raise ValueError("controlled patch content is too large")

class CapabilityGrantError(ValueError):
    pass


_HEADER_KEYS = {"schema_version", "alg", "typ", "kid"}
_PAYLOAD_KEYS = {
    "grant_type", "grant_id", "issuer", "audience", "ticket_id", "decision_id",
    "decision_sha256", "impact_sha256", "action_permission_sha256",
    "action_registry_sha256", "capability_manifest_hash", "component_manifest_hash",
    "action_id", "action_version", "arguments_sha256", "workspace_id",
    "workspace_scope_hash", "principal_scope_hash", "risk_class",
    "allowed_side_effects", "path_policy", "allow_absolute_paths", "allow_shell",
    "allow_python", "confirmation_sha256", "skill_id", "skill_version",
    "skill_sha256", "skill_activation_sha256", "gateway_epoch", "nonce",
    "issued_at_ms", "not_before_ms", "expires_at_ms",
}
# vNext 授权链新增的可选字段（草案 §2.4 grant vNext 绑定）：
# 旧签发体不含这些键，新签发体恒含；消费端按"必需键齐备 + 无未知键"校验。
_VNEXT_OPTIONAL_PAYLOAD_KEYS = {
    "conversation_scope_hash", "effect_id", "generation",
    "request_id", "run_id", "ticket_sha256", "composition_execution_binding",
}
_COMPOSITION_BINDING_REQUIRED_KEYS = {
    "schema_version", "binding_type", "executable_plan_id",
    "executable_plan_sha256", "step_id", "step_binding_sha256", "request_id",
    "run_id", "generation", "effect_id", "action_id", "action_version",
    "materialized_arguments_sha256", "canonical_invocation_sha256",
    "target_sha256", "workspace_id", "workspace_scope_hash", "binding_sha256",
}
_COMPOSITION_CONTINUATION_KEYS = {
    "attempt", "continuation_delegation_id", "continuation_delegation_sha256",
    "dependency_evidence_sha256",
}
_COMPOSITION_PREDECESSOR_KEYS = {
    "supersedes_authorization_id", "supersedes_effect_id", "supersedes_claim_sha256",
}
_COMPOSITION_BINDING_OPTIONAL_KEYS = (
    {"target_snapshot_sha256", "execution_profile_id", "execution_profile_sha256"}
    | _COMPOSITION_CONTINUATION_KEYS
    | _COMPOSITION_PREDECESSOR_KEYS
)
_COMPOSITION_BINDING_REQUIRED_SHA_KEYS = {
    "executable_plan_sha256", "step_binding_sha256", "materialized_arguments_sha256",
    "canonical_invocation_sha256", "target_sha256", "workspace_scope_hash",
    "binding_sha256",
}
_SHA_FIELDS = {
    "decision_sha256", "impact_sha256", "action_permission_sha256",
    "action_registry_sha256", "capability_manifest_hash", "component_manifest_hash",
    "arguments_sha256", "workspace_scope_hash", "principal_scope_hash",
}
_SIDE_EFFECTS = {"none", "read", "local_write", "external_write", "external_send", "destructive"}
_TRUST_BUNDLE_KEYS = {
    "schema_version", "bundle_id", "revision", "gateway_epoch", "generated_at_ms",
    "required_scopes", "keys", "production_ready", "bundle_sha256",
}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CapabilityGrantError("capability JSON contains duplicate keys")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    def walk(item: Any) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            raise CapabilityGrantError("capability JSON floats are forbidden")
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise CapabilityGrantError("capability JSON keys must be strings")
            for child in item.values():
                walk(child)
            return
        raise CapabilityGrantError("capability JSON type is unsupported")

    walk(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def workspace_scope_hash(workspace: str) -> str:
    normalized = os.path.normcase(
        unicodedata.normalize("NFC", str(Path(workspace).resolve(strict=True)))
    )
    return _sha({"normalized_workspace": normalized})


def invocation_arguments_sha256(action: str, target: str, args: Mapping[str, Any]) -> str:
    return _sha({"action": action, "args": dict(args), "target": target})


def _safe_nonce_root(path_text: str) -> Path:
    candidate = Path(path_text).expanduser()
    if not candidate.is_absolute() or candidate == Path(candidate.anchor):
        raise CapabilityGrantError("Omni nonce path is unsafe")
    # Reject an explicitly configured symlink before creating anything. Parent
    # directories may be platform-managed junctions; the resolved target is
    # checked again below and remains outside model control.
    if candidate.exists() and candidate.is_symlink():
        raise CapabilityGrantError("Omni nonce path is unsafe")
    candidate.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=True)
    if resolved == Path(resolved.anchor) or not resolved.is_dir() or resolved.is_symlink():
        raise CapabilityGrantError("Omni nonce path is unsafe")
    return resolved


def _runtime_nonce_root() -> Path:
    explicit = str(os.environ.get("TIANGONG_OMNI_NONCE_ROOT") or "").strip()
    if explicit:
        return _safe_nonce_root(explicit)
    state_root = str(os.environ.get("TIANGONG_OMNI_BODY_STATE_ROOT") or "").strip()
    if not state_root:
        raise CapabilityGrantError("Omni nonce root is unavailable")
    root = Path(state_root).expanduser()
    if not root.is_absolute() or root == Path(root.anchor):
        raise CapabilityGrantError("Omni nonce path is unsafe")
    return _safe_nonce_root(str(root / "capability_nonces"))


def _validate_trust_bundle(
    bundle: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_epoch: int,
) -> dict[str, Any]:
    normalized = dict(bundle)
    if set(normalized) != _TRUST_BUNDLE_KEYS:
        raise CapabilityGrantError("Omni trust bundle fields are invalid")
    if normalized.get("production_ready") is not True:
        raise CapabilityGrantError("Omni trust bundle is not production ready")
    declared = str(normalized.get("bundle_sha256") or "")
    computed = _sha({key: value for key, value in normalized.items() if key != "bundle_sha256"})
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
        or declared != computed
        or declared != expected_sha
        or type(normalized.get("gateway_epoch")) is not int
        or normalized.get("gateway_epoch") != expected_epoch
    ):
        raise CapabilityGrantError("Omni trust bundle pin or epoch is invalid")
    scopes = normalized.get("required_scopes")
    keys = normalized.get("keys")
    if not isinstance(scopes, list) or not isinstance(keys, list) or not keys:
        raise CapabilityGrantError("Omni trust bundle scope or key set is invalid")
    required_scope = {
        "issuer": "tiangong-total-gateway",
        "audience": "tiangong-backend",
        "purpose": "execution_ticket",
    }
    if required_scope not in scopes:
        raise CapabilityGrantError("Omni trust bundle scope is invalid")
    return normalized


def _load_trust(runtime_meta: Mapping[str, Any]) -> tuple[dict[str, Any], int, Path]:
    inline_bundle = runtime_meta.get("trust_bundle")
    inline_sha = runtime_meta.get("trust_bundle_sha256")
    inline_epoch = runtime_meta.get("gateway_epoch")
    inline_present = any(value is not None for value in (inline_bundle, inline_sha, inline_epoch))
    if inline_present:
        if (
            not isinstance(inline_bundle, Mapping)
            or not isinstance(inline_sha, str)
            or type(inline_epoch) is not int
            or inline_epoch < 1
        ):
            raise CapabilityGrantError("Omni inline trust pin is incomplete")
        bundle = _validate_trust_bundle(
            inline_bundle,
            expected_sha=inline_sha,
            expected_epoch=inline_epoch,
        )
        return bundle, inline_epoch, _runtime_nonce_root()

    path_text = str(os.environ.get("TIANGONG_OMNI_TRUST_BUNDLE_PATH") or "")
    expected_sha = str(os.environ.get("TIANGONG_OMNI_TRUST_BUNDLE_SHA256") or "")
    epoch_text = str(os.environ.get("TIANGONG_OMNI_GATEWAY_EPOCH") or "")
    nonce_text = str(os.environ.get("TIANGONG_OMNI_NONCE_ROOT") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or not epoch_text.isdecimal():
        raise CapabilityGrantError("Omni trust pin is unavailable")
    path = Path(path_text)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise CapabilityGrantError("Omni trust or nonce path is unsafe")
    raw = path.read_bytes()
    if not raw or len(raw) > 262_144:
        raise CapabilityGrantError("Omni trust bundle size is invalid")
    try:
        bundle = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(CapabilityGrantError("non-finite trust value")),
        )
    except CapabilityGrantError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityGrantError("Omni trust bundle is invalid") from exc
    if not isinstance(bundle, dict):
        raise CapabilityGrantError("Omni trust bundle is invalid")
    normalized = _validate_trust_bundle(
        bundle,
        expected_sha=expected_sha,
        expected_epoch=int(epoch_text),
    )
    return normalized, int(epoch_text), _safe_nonce_root(nonce_text)


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
    except ValueError as exc:
        raise CapabilityGrantError("capability base64url is invalid") from exc


def _consume_nonce(root: Path, payload: Mapping[str, Any], grant_sha256: str) -> None:
    nonce = str(payload.get("nonce") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", nonce):
        raise CapabilityGrantError("capability nonce is invalid")
    receipt = root / (hashlib.sha256(nonce.encode("utf-8")).hexdigest() + ".json")
    data = _canonical(
        {
            "expires_at_ms": payload["expires_at_ms"],
            "gateway_epoch": payload["gateway_epoch"],
            "grant_sha256": grant_sha256,
            "nonce": nonce,
        }
    )
    try:
        descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CapabilityGrantError("capability nonce replay is forbidden") from exc
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_composition_continuation(composition: Mapping[str, Any]) -> None:
    """Verify the existing P7D.2 wire shape, not continuation eligibility.

    The Gateway/Store still own issuance, predecessor CAS and dispatch. This
    consumer requires the complete signed coordinates and verifies their hash
    and signature below; it never grants a retry from these fields alone.
    """
    keys = set(composition)
    continuation = keys & _COMPOSITION_CONTINUATION_KEYS
    predecessor = keys & _COMPOSITION_PREDECESSOR_KEYS
    if not continuation and not predecessor:
        return  # Existing single-step P7D.1 binding.
    if continuation != _COMPOSITION_CONTINUATION_KEYS:
        raise CapabilityGrantError("capability composition continuation is incomplete")
    attempt = composition["attempt"]
    if type(attempt) is not int or attempt < 1:
        raise CapabilityGrantError("capability composition attempt is invalid")
    if (attempt == 1 and predecessor) or (
        attempt > 1 and predecessor != _COMPOSITION_PREDECESSOR_KEYS
    ):
        raise CapabilityGrantError("capability composition predecessor is invalid")
    patterns = {
        "continuation_delegation_id": r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}",
        "continuation_delegation_sha256": r"[0-9a-f]{64}",
        "dependency_evidence_sha256": r"[0-9a-f]{64}",
    }
    if predecessor:
        patterns.update({
            "supersedes_authorization_id": r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}",
            "supersedes_effect_id": r"eff_[0-9a-f]{64}",
            "supersedes_claim_sha256": r"[0-9a-f]{64}",
        })
    if any(
        not isinstance(composition[field], str)
        or re.fullmatch(pattern, composition[field]) is None
        for field, pattern in patterns.items()
    ):
        raise CapabilityGrantError("capability composition continuation fields are invalid")


def verify_capability_grant(
    grant: Mapping[str, Any],
    *,
    action: str,
    target: str,
    args: Mapping[str, Any],
    workspace: str,
    runtime_meta: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as exc:
        raise CapabilityGrantError("Ed25519 verifier is unavailable") from exc
    bundle, epoch, nonce_root = _load_trust(runtime_meta)
    if set(grant) != {"header", "payload", "signature"}:
        raise CapabilityGrantError("capability grant envelope is invalid")
    header = grant.get("header")
    payload = grant.get("payload")
    signature = grant.get("signature")
    if not isinstance(header, Mapping) or not isinstance(payload, Mapping) or not isinstance(signature, str):
        raise CapabilityGrantError("capability grant shape is invalid")
    if set(header) != _HEADER_KEYS:
        raise CapabilityGrantError("capability grant header fields are incomplete or unknown")
    payload_keys = set(payload)
    missing_keys = _PAYLOAD_KEYS - payload_keys
    unknown_keys = payload_keys - _PAYLOAD_KEYS - _VNEXT_OPTIONAL_PAYLOAD_KEYS
    if missing_keys or unknown_keys:
        raise CapabilityGrantError("capability grant fields are incomplete or unknown")
    if (
        header.get("schema_version") not in {"tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"}
        or header.get("alg") != "EdDSA"
        or header.get("typ") != "tiangong.omni-capability-grant+jws"
    ):
        raise CapabilityGrantError("capability grant header is invalid")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(payload.get(key) or "")) for key in _SHA_FIELDS):
        raise CapabilityGrantError("capability grant digest field is invalid")
    side_effects = payload.get("allowed_side_effects")
    if (
        not isinstance(side_effects, list)
        or side_effects != sorted(set(side_effects))
        or any(item not in _SIDE_EFFECTS for item in side_effects)
        or payload.get("path_policy") not in {"no_path", "workspace_only", "object_grant_only"}
        or any(type(payload.get(key)) is not bool for key in ("allow_absolute_paths", "allow_shell", "allow_python"))
    ):
        raise CapabilityGrantError("capability permission envelope is invalid")
    now_ms = time.time_ns() // 1_000_000
    if (
        payload.get("grant_type") != "OmniCapabilityGrant"
        or payload.get("issuer") != "tiangong-total-gateway"
        or payload.get("audience") != "tiangong-backend"
        or payload.get("gateway_epoch") != epoch
        or type(payload.get("issued_at_ms")) is not int
        or type(payload.get("not_before_ms")) is not int
        or type(payload.get("expires_at_ms")) is not int
        or not payload["issued_at_ms"] <= payload["not_before_ms"] <= now_ms <= payload["expires_at_ms"]
        or payload["expires_at_ms"] - payload["issued_at_ms"] > 60_000
    ):
        raise CapabilityGrantError("capability grant time or authority is invalid")
    if payload.get("action_id") != action:
        raise CapabilityGrantError("capability action binding is invalid")
    if payload.get("arguments_sha256") != invocation_arguments_sha256(action, target, args):
        raise CapabilityGrantError("capability argument binding is invalid")
    if payload.get("workspace_scope_hash") != workspace_scope_hash(workspace):
        raise CapabilityGrantError("capability workspace binding is invalid")
    required_runtime = {
        "execution_ticket_id": "ticket_id",
        "principal_scope_hash": "principal_scope_hash",
        "workspace_id": "workspace_id",
        "action_version": "action_version",
        "decision_sha256": "decision_sha256",
        "impact_sha256": "impact_sha256",
        "action_permission_sha256": "action_permission_sha256",
        "action_registry_sha256": "action_registry_sha256",
        "capability_manifest_hash": "capability_manifest_hash",
        "component_manifest_hash": "component_manifest_hash",
    }
    for runtime_key, payload_key in required_runtime.items():
        if not runtime_meta.get(runtime_key) or runtime_meta.get(runtime_key) != payload.get(payload_key):
            raise CapabilityGrantError(f"capability runtime binding is invalid: {runtime_key}")
    payload_has_composition = "composition_execution_binding" in payload
    composition = payload.get("composition_execution_binding")
    runtime_has_composition = "composition_execution_binding" in runtime_meta
    if not payload_has_composition:
        if runtime_has_composition:
            raise CapabilityGrantError("capability composition binding is incomplete")
    else:
        runtime_composition = runtime_meta.get("composition_execution_binding")
        if (
            not isinstance(composition, Mapping)
            or not runtime_has_composition
            or not isinstance(runtime_composition, Mapping)
        ):
            raise CapabilityGrantError("capability composition binding is incomplete")
        composition_keys = set(composition)
        if (
            _COMPOSITION_BINDING_REQUIRED_KEYS - composition_keys
            or composition_keys
            - _COMPOSITION_BINDING_REQUIRED_KEYS
            - _COMPOSITION_BINDING_OPTIONAL_KEYS
        ):
            raise CapabilityGrantError("capability composition binding fields are invalid")
        _validate_composition_continuation(composition)
        if (
            composition.get("schema_version")
            != "tiangong.composition-execution-binding.v1"
            or composition.get("binding_type") != "COMPOSITION_STEP"
            or type(composition.get("generation")) is not int
            or composition.get("generation", -1) < 0
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", str(composition.get(key) or ""))
                for key in _COMPOSITION_BINDING_REQUIRED_SHA_KEYS
            )
            or (
                "target_snapshot_sha256" in composition
                and not re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(composition.get("target_snapshot_sha256") or ""),
                )
            )
            or not re.fullmatch(r"req_[0-9a-f]{64}", str(composition.get("request_id") or ""))
            or not re.fullmatch(r"run_[0-9a-f]{64}", str(composition.get("run_id") or ""))
            or not re.fullmatch(r"eff_[0-9a-f]{64}", str(composition.get("effect_id") or ""))
            or any(
                not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}",
                    str(composition.get(key) or ""),
                )
                for key in (
                    "executable_plan_id", "step_id", "action_version", "workspace_id"
                )
            )
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._:-]{0,159}",
                str(composition.get("action_id") or ""),
            )
        ):
            raise CapabilityGrantError("capability composition binding fields are invalid")
        computed_binding_sha256 = _sha(
            {
                key: value
                for key, value in composition.items()
                if key != "binding_sha256"
            }
        )
        if composition.get("binding_sha256") != computed_binding_sha256:
            raise CapabilityGrantError("capability composition binding digest is invalid")
        if _canonical(dict(runtime_composition)) != _canonical(dict(composition)):
            raise CapabilityGrantError("capability runtime composition binding is invalid")
        required_composition_runtime = {
            "request_id": "request_id",
            "run_id": "run_id",
            "generation": "generation",
            "effect_id": "effect_id",
            "step_id": "step_id",
            "executable_plan_id": "executable_plan_id",
            "composition_binding_sha256": "binding_sha256",
        }
        if any(
            runtime_key not in runtime_meta
            or _canonical(runtime_meta.get(runtime_key))
            != _canonical(composition.get(binding_key))
            for runtime_key, binding_key in required_composition_runtime.items()
        ):
            raise CapabilityGrantError("capability runtime composition scope is invalid")
        if (
            composition.get("request_id") != payload.get("request_id")
            or composition.get("run_id") != payload.get("run_id")
            or composition.get("generation") != payload.get("generation")
            or composition.get("effect_id") != payload.get("effect_id")
            or composition.get("action_id") != payload.get("action_id")
            or composition.get("action_version") != payload.get("action_version")
            or composition.get("workspace_id") != payload.get("workspace_id")
            or composition.get("workspace_scope_hash")
            != payload.get("workspace_scope_hash")
        ):
            raise CapabilityGrantError("capability composition binding scope is invalid")
        if composition.get("target_sha256") != _sha(target):
            raise CapabilityGrantError("capability composition target binding is invalid")
        if composition.get("materialized_arguments_sha256") != _sha(dict(args)):
            raise CapabilityGrantError("capability composition argument binding is invalid")
        if not composition_authority_allowed(
            action_id=action, risk_class=payload.get("risk_class"),
            allowed_side_effects=side_effects,
            allow_shell=payload.get("allow_shell"), allow_python=payload.get("allow_python"),
            **composition_profile_fields(composition),
        ):
            raise CapabilityGrantError(
                "composition capability exceeds the A0 read-only ceiling or signed execution profile"
            )
    if payload.get("risk_class") not in {"A0", "A1", "A2", "A3", "A4"}:
        raise CapabilityGrantError("A5 capability is forbidden")
    # Complete-mode policy: A0-A4 are automatically executable and A5 can
    # never receive a capability grant.  A model/user supplied confirmation
    # hash is therefore not authority and is rejected as a stale contract.
    if payload.get("confirmation_sha256") is not None:
        raise CapabilityGrantError("A0-A4 capability grants must not carry confirmation")
    if runtime_meta.get("confirmation_sha256") not in {None, ""}:
        raise CapabilityGrantError("capability runtime confirmation is forbidden")
    skill_values = tuple(payload.get(key) for key in ("skill_id", "skill_version", "skill_sha256"))
    if sum(value is not None for value in skill_values) not in {0, 3}:
        raise CapabilityGrantError("capability Skill identity is incomplete")
    if (payload.get("skill_id") is None) != (payload.get("skill_activation_sha256") is None):
        raise CapabilityGrantError("capability Skill activation binding is incomplete")
    if payload.get("skill_id") is not None:
        for key in ("skill_id", "skill_version", "skill_sha256", "skill_activation_sha256"):
            if runtime_meta.get(key) != payload.get(key):
                raise CapabilityGrantError("capability Skill binding is invalid")
    elif any(runtime_meta.get(key) is not None for key in ("skill_id", "skill_version", "skill_sha256", "skill_activation_sha256")):
        raise CapabilityGrantError("unskilled capability cannot cross into a Skill runtime")
    kid = str(header.get("kid") or "")
    key = next((item for item in list(bundle.get("keys") or []) if isinstance(item, dict) and item.get("kid") == kid), None)
    if (
        key is None
        or key.get("issuer") != payload.get("issuer")
        or key.get("audience") != payload.get("audience")
        or key.get("purpose") != "execution_ticket"
        or key.get("state") not in {"ACTIVE", "PREVIOUS"}
        or not key.get("not_before_ms") <= now_ms < key.get("not_after_ms")
        or key.get("component_manifest_hash") != payload.get("component_manifest_hash")
    ):
        raise CapabilityGrantError("capability signing key is not trusted")
    signing_input = (
        base64.urlsafe_b64encode(_canonical(dict(header))).rstrip(b"=")
        + b"."
        + base64.urlsafe_b64encode(_canonical(dict(payload))).rstrip(b"=")
    )
    try:
        Ed25519PublicKey.from_public_bytes(_decode(str(key.get("public_key_base64url") or ""))).verify(
            _decode(signature), signing_input
        )
    except (ValueError, InvalidSignature) as exc:
        raise CapabilityGrantError("capability signature is invalid") from exc
    grant_sha256 = _sha(dict(grant))
    verified = {
        "allow_absolute_paths": payload.get("allow_absolute_paths") is True,
        "allow_shell": payload.get("allow_shell") is True,
        "allow_python": payload.get("allow_python") is True,
        "confirmed": False,
        "grant_id": str(payload.get("grant_id") or ""),
        "grant_sha256": grant_sha256,
    }
    if payload_has_composition:
        # Preserve the exact signed scope for the API wrapper's runtime policy.
        # Detach it from caller-owned mappings after verification; the wrapper
        # must never re-read raw/model metadata to recover these restrictions.
        verified["composition_execution_binding"] = json.loads(_canonical(dict(composition)))
    _consume_nonce(nonce_root, payload, grant_sha256)
    return verified


__all__ = [
    "CapabilityGrantError",
    "invocation_arguments_sha256",
    "verify_capability_grant",
    "workspace_scope_hash",
]
