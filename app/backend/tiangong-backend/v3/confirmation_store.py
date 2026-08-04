"""Pending user confirmations and signed v3 confirmation grants.

The v3 permission layer (permission_settings.check_tool_permission) decides
*whether* an action may proceed; this store owns the lifecycle of an
out-of-band user confirmation:

* ``create_pending`` parks a request that needs the user's explicit decision.
  Records live in memory and are persisted to
  ``~/.tiangong/v3/pending_confirmations.json`` so a restart cannot silently
  drop them.  Pending records wait up to 24 hours (LangGraph interrupt 模式：
  审批挂起可长期等待、可恢复)；超时即 expired (fail closed)，且前端必须
  显式呈现过期状态（Microsoft Agent Framework：TTL 过期=可见的自动拒绝）。
* ``resolve`` turns a user decision into either a signed grant
  (once/session/always) or a refusal (deny/expired).
* Grants are Ed25519 signatures over canonical JSON, the same signing
  mechanism the gateway uses for Omni capability grants
  (``total_gateway.tickets.TicketSigner`` / ``runtime_security``
  b64url(header).b64url(payload) signing input).  Every grant is bound to
  (principal_scope_hash, action, normalized_target, expiry); a grant can only
  be minted here, never from model-supplied fields.

The gateway side (total_gateway.omni_grant_authority / policy_engine) calls
``gateway_path_evidence`` / ``verify_confirmation_grant`` in-process; both
fail closed when no valid evidence exists.
"""
from __future__ import annotations

# ── G3 确认退役（草案 §4.2）──────────────────────────────────────────────
# 本模块属旧 confirmation 链的孤儿代码：/api/v1/policy/confirm 已固定返回
# HTTP 410 + POLICY_CONFIRMATION_RETIRED，本模块不再有生产签发/消费调用方。
# 保留文件仅供历史归档与后续 PROD-MIGRATE 处置；
# 恢复旧快照时必须向前合并 retirement fact，禁止在此恢复任何批准能力。

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

try:  # same canonical form as the gateway contracts
    from contracts import canonical_json_bytes
except Exception:  # pragma: no cover - standalone backend fallback

    def canonical_json_bytes(value: Any) -> bytes:
        def sort_key(text: str) -> bytes:
            return text.encode("utf-16-be")

        def normalize(item: Any) -> Any:
            if item is None or isinstance(item, (str, bool, int)):
                return item
            if isinstance(item, Mapping):
                return {key: normalize(item[key]) for key in sorted(item, key=sort_key)}
            if isinstance(item, (list, tuple)):
                return [normalize(entry) for entry in item]
            raise TypeError(f"unsupported canonical JSON value: {type(item).__name__}")

        return json.dumps(
            normalize(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")


try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except Exception:  # pragma: no cover - cryptography ships with the runtime
    Ed25519PrivateKey = None  # type: ignore[assignment]
    Ed25519PublicKey = None  # type: ignore[assignment]

from .run_context import current_run_context


PENDING_TTL_MS = 86_400_000  # 审批挂起最长等待 24 小时（原 300 秒过短，用户"过会再点"必落空）
ONCE_GRANT_TTL_MS = 600_000
SESSION_GRANT_TTL_MS = 1_800_000
EXEMPTION_TTL_MS = 120_000  # 本轮用户指定豁免只在当轮有效
GRANT_TYPE = "tiangong.v3.confirmation-grant.v1"
GRANT_HEADER_TYP = "tiangong.v3-confirmation-grant+jws"
GRANT_ISSUER = "tiangong-v3-confirmation-store"
_VALID_DECISIONS = {"once", "session", "always", "deny", "expired"}

_LOCK = threading.RLock()
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_LOADED = False
_EXEMPTIONS: list[dict[str, Any]] = []
_KEYPAIR: dict[str, Any] | None = None


def _state_dir() -> Path:
    override = str(os.environ.get("TIANGONG_V3_STATE_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return Path.home() / ".tiangong" / "v3"


def _pending_path() -> Path:
    return _state_dir() / "pending_confirmations.json"


def _key_path() -> Path:
    return _state_dir() / "confirmation_signing_key.json"


def _verification_key_path() -> Path:
    return _state_dir() / "confirmation_verification_key.json"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def normalize_path_text(value: Any) -> str:
    """One canonical form shared by the v3 layer and the gateway evidence check."""
    text = str(value or "").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    if os.name == "nt":
        text = text.replace("/", "\\")
    else:
        text = text.replace("\\", "/")
    try:
        return os.path.normcase(
            str(Path(os.path.expandvars(text)).expanduser().resolve(strict=False))
        )
    except Exception:
        return os.path.normcase(text)


def _same_or_under(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        left = normalize_path_text(path)
        right = normalize_path_text(root)
        return os.path.commonpath([left, right]) == right
    except Exception:
        return False


def _paths_match(path: str, bound: str) -> bool:
    left = normalize_path_text(path)
    right = normalize_path_text(bound)
    if not left or not right:
        return False
    return left == right or _same_or_under(left, right) or _same_or_under(right, left)


def _persist_pending_locked() -> None:
    path = _pending_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "records": list(_PENDING.values())}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except Exception:
        # Persistence guards against restart loss only; never fail an
        # in-memory authorization flow because the disk is unhappy.
        pass


def _load_pending_locked() -> None:
    global _PENDING_LOADED
    if _PENDING_LOADED:
        return
    _PENDING_LOADED = True
    path = _pending_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return
        for record in records:
            if isinstance(record, dict) and record.get("confirm_id"):
                _PENDING[str(record["confirm_id"])] = record
    except Exception:
        return


def _sweep_expired_locked(now_ms: int) -> bool:
    changed = False
    for record in _PENDING.values():
        if record.get("status") == "pending" and now_ms > int(record.get("expires_at_ms") or 0):
            record["status"] = "expired"
            record["resolved_at_ms"] = now_ms
            changed = True
    if changed:
        _persist_pending_locked()
    return changed


def _run_identity() -> tuple[str, str]:
    try:
        context = current_run_context()
        return (
            str(context.principal_scope_hash or ""),
            str(context.session_id or ""),
        )
    except Exception:
        return "", ""


def _ensure_keypair() -> dict[str, Any]:
    """Load or create the confirmation signing key (Ed25519, same as gateway)."""
    global _KEYPAIR
    with _LOCK:
        if _KEYPAIR is not None:
            return _KEYPAIR
        if Ed25519PrivateKey is None:
            raise RuntimeError("confirmation_signing_unavailable")
        key_file = _key_path()
        if key_file.exists():
            try:
                data = json.loads(key_file.read_text(encoding="utf-8-sig"))
                private = Ed25519PrivateKey.from_private_bytes(
                    _b64url_decode(str(data["private_key_base64url"]))
                )
                _KEYPAIR = {"kid": str(data["kid"]), "private": private}
                return _KEYPAIR
            except Exception:
                # A corrupt key file must not resurrect old grants: fail closed.
                raise RuntimeError("confirmation_signing_key_invalid")
        private = Ed25519PrivateKey.generate()
        public_bytes = private.public_key().public_bytes_raw()
        kid = "v3cfm_" + hashlib.sha256(public_bytes).hexdigest()[:16]
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(
            json.dumps(
                {
                    "kid": kid,
                    "private_key_base64url": _b64url(private.private_bytes_raw()),
                    "public_key_base64url": _b64url(public_bytes),
                    "created_at_ms": _now_ms(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
        _verification_key_path().write_text(
            json.dumps(
                {
                    "kid": kid,
                    "public_key_base64url": _b64url(public_bytes),
                    "created_at_ms": _now_ms(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _KEYPAIR = {"kid": kid, "private": private}
        return _KEYPAIR


def _load_verification_key(kid: str) -> Any:
    """Verification side only ever needs the public half."""
    if Ed25519PublicKey is None:
        raise RuntimeError("confirmation_verification_unavailable")
    for path in (_verification_key_path(), _key_path()):
        try:
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if str(data.get("kid") or "") != kid:
                continue
            return Ed25519PublicKey.from_public_bytes(
                _b64url_decode(str(data["public_key_base64url"]))
            )
        except Exception:
            continue
    raise RuntimeError("confirmation_verification_key_missing")


def _sign_grant(payload: dict[str, Any]) -> dict[str, Any]:
    keypair = _ensure_keypair()
    header = {"alg": "EdDSA", "typ": GRANT_HEADER_TYP, "kid": keypair["kid"]}
    signing_input = (
        _b64url(canonical_json_bytes(header)) + "." + _b64url(canonical_json_bytes(payload))
    ).encode("ascii")
    signature = _b64url(keypair["private"].sign(signing_input))
    return {"header": header, "payload": payload, "signature": signature}


def create_pending(action: str, target: str, risk: str, summary: str) -> str:
    """Park one confirmation request; returns its confirm_id."""
    now = _now_ms()
    principal, session_id = _run_identity()
    confirm_id = "cfm_" + secrets.token_hex(16)
    record = {
        "confirm_id": confirm_id,
        "action": str(action or "").strip(),
        "target": normalize_path_text(target),
        "risk": str(risk or "").strip() or "A1",
        "summary": str(summary or "").strip(),
        "status": "pending",
        "decision": None,
        "created_at_ms": now,
        "expires_at_ms": now + PENDING_TTL_MS,
        "principal_scope_hash": principal,
        "session_id": session_id,
        "issuer": None,
        "resolved_at_ms": None,
        "consumed_at_ms": None,
        "grant": None,
    }
    with _LOCK:
        _load_pending_locked()
        _sweep_expired_locked(now)
        _PENDING[confirm_id] = record
        _persist_pending_locked()
    return confirm_id


def get_pending(confirm_id: str) -> dict[str, Any] | None:
    with _LOCK:
        _load_pending_locked()
        _sweep_expired_locked(_now_ms())
        record = _PENDING.get(str(confirm_id or ""))
        return dict(record) if isinstance(record, dict) else None


def list_pending() -> list[dict[str, Any]]:
    with _LOCK:
        _load_pending_locked()
        _sweep_expired_locked(_now_ms())
        return [
            dict(record)
            for record in _PENDING.values()
            if record.get("status") == "pending"
        ]


def _mint_grant(record: dict[str, Any], decision: str, issuer: str, now: int) -> dict[str, Any]:
    ttl = SESSION_GRANT_TTL_MS if decision in {"session", "always"} else ONCE_GRANT_TTL_MS
    payload = {
        "grant_type": GRANT_TYPE,
        "confirm_id": str(record["confirm_id"]),
        "decision": decision,
        "action": str(record.get("action") or ""),
        "target": str(record.get("target") or ""),
        "principal_scope_hash": str(record.get("principal_scope_hash") or ""),
        "session_id": str(record.get("session_id") or ""),
        "issuer": issuer or GRANT_ISSUER,
        "nonce": secrets.token_hex(16),
        "issued_at_ms": now,
        "expires_at_ms": now + ttl,
    }
    return _sign_grant(payload)


def _persist_allow_root(target: str) -> str:
    """always 决策：把目标根目录持久化进 permission_settings 的 allow_roots。"""
    from .permission_settings import baocun_permission_settings, duqu_permission_settings

    normalized = normalize_path_text(target)
    root = normalized
    try:
        suffix = PureWindowsPath(normalized).suffix
        if suffix:
            root = str(PureWindowsPath(normalized).parent)
    except Exception:
        pass
    settings = duqu_permission_settings()
    roots = list(settings.get("allow_roots") or [])
    if not any(_paths_match(root, existing) for existing in roots):
        roots.append(root)
        baocun_permission_settings({"allow_roots": roots})
    return root


def resolve(confirm_id: str, decision: str, *, issuer: Any = None) -> dict[str, Any]:
    """Apply a user decision; mint a signed grant for once/session/always."""
    decision_text = str(decision or "").strip().lower()
    issuer_text = str(issuer or GRANT_ISSUER)
    now = _now_ms()
    with _LOCK:
        _load_pending_locked()
        _sweep_expired_locked(now)
        record = _PENDING.get(str(confirm_id or ""))
        if record is None:
            return {"granted": False, "grant": None, "reason": "确认请求不存在或已被清理。"}
        if decision_text not in _VALID_DECISIONS:
            return {"granted": False, "grant": None, "reason": f"无效的确认决策: {decision_text}"}
        if record.get("status") != "pending":
            return {
                "granted": False,
                "grant": None,
                "reason": f"确认请求已处理（{record.get('status')}），不能重复决策。",
            }
        if decision_text in {"deny", "expired"}:
            record["status"] = "denied" if decision_text == "deny" else "expired"
            record["decision"] = decision_text
            record["issuer"] = issuer_text
            record["resolved_at_ms"] = now
            _persist_pending_locked()
            reason = "用户拒绝了这次操作。" if decision_text == "deny" else "确认请求已超时。"
            return {"granted": False, "grant": None, "reason": reason}
        try:
            grant = _mint_grant(record, decision_text, issuer_text, now)
        except Exception as exc:
            return {"granted": False, "grant": None, "reason": f"授权签发失败: {exc}"}
        record["status"] = "granted"
        record["decision"] = decision_text
        record["issuer"] = issuer_text
        record["resolved_at_ms"] = now
        record["grant"] = grant
        if decision_text == "always":
            try:
                record["allow_root"] = _persist_allow_root(str(record.get("target") or ""))
            except Exception as exc:
                record["allow_root_error"] = str(exc)
        _persist_pending_locked()
    return {"granted": True, "grant": grant, "reason": "用户已授权。"}


def verify_confirmation_grant(
    grant: Any,
    *,
    action: str = "",
    target: str = "",
    principal_scope_hash: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Verify signature + binding of a signed confirmation grant.

    Structural garbage (including booleans smuggled in as 'confirmation') is
    rejected before any cryptography runs.
    """
    now = _now_ms() if now_ms is None else int(now_ms)
    if not isinstance(grant, Mapping):
        return {"ok": False, "reason": "grant_not_an_object", "payload": None}
    header = grant.get("header")
    payload = grant.get("payload")
    signature = grant.get("signature")
    if not isinstance(header, Mapping) or not isinstance(payload, Mapping):
        return {"ok": False, "reason": "grant_structure_invalid", "payload": None}
    if (
        header.get("alg") != "EdDSA"
        or header.get("typ") != GRANT_HEADER_TYP
        or not isinstance(signature, str)
        or not signature
    ):
        return {"ok": False, "reason": "grant_header_invalid", "payload": None}
    if payload.get("grant_type") != GRANT_TYPE:
        return {"ok": False, "reason": "grant_type_invalid", "payload": None}
    try:
        issued = int(payload.get("issued_at_ms"))
        expires = int(payload.get("expires_at_ms"))
    except Exception:
        return {"ok": False, "reason": "grant_time_invalid", "payload": None}
    if not issued <= now <= expires:
        return {"ok": False, "reason": "grant_expired", "payload": None}
    if action and str(payload.get("action") or "") != str(action):
        return {"ok": False, "reason": "grant_action_mismatch", "payload": None}
    if principal_scope_hash is not None and str(
        payload.get("principal_scope_hash") or ""
    ) != str(principal_scope_hash):
        return {"ok": False, "reason": "grant_principal_mismatch", "payload": None}
    if target and not _paths_match(target, str(payload.get("target") or "")):
        return {"ok": False, "reason": "grant_target_mismatch", "payload": None}
    try:
        public = _load_verification_key(str(header.get("kid") or ""))
        signing_input = (
            _b64url(canonical_json_bytes(dict(header)))
            + "."
            + _b64url(canonical_json_bytes(dict(payload)))
        ).encode("ascii")
        public.verify(_b64url_decode(signature), signing_input)
    except Exception:
        return {"ok": False, "reason": "grant_signature_invalid", "payload": None}
    return {"ok": True, "reason": "", "payload": dict(payload)}


def _live_grant_records_locked(action: str, principal: str, now: int) -> list[dict[str, Any]]:
    records = []
    for record in _PENDING.values():
        if record.get("status") != "granted" or not isinstance(record.get("grant"), dict):
            continue
        if record.get("decision") == "once" and record.get("consumed_at_ms"):
            # once 授权消费即作废，任何重放一律失效（fail closed）。
            continue
        if str(record.get("action") or "") != str(action):
            continue
        if str(record.get("principal_scope_hash") or "") != str(principal):
            continue
        payload = record["grant"].get("payload") if isinstance(record["grant"], dict) else None
        if not isinstance(payload, dict):
            continue
        try:
            if not int(payload.get("issued_at_ms")) <= now <= int(payload.get("expires_at_ms")):
                continue
        except Exception:
            continue
        records.append(record)
    return records


def find_live_grant(action: str, target: str, principal_scope_hash: str = "") -> dict[str, Any] | None:
    """A granted, unexpired, unconsumed grant covering (action, target)."""
    now = _now_ms()
    with _LOCK:
        _load_pending_locked()
        for record in _live_grant_records_locked(action, principal_scope_hash, now):
            if record.get("decision") == "once" and record.get("consumed_at_ms"):
                continue
            if _paths_match(target, str(record.get("target") or "")):
                return record["grant"]
    return None


def consume_once_grant(confirm_id: str) -> bool:
    """Mark a once-grant used; replay afterwards fails closed."""
    text = str(confirm_id or "")
    if not text:
        return False
    with _LOCK:
        _load_pending_locked()
        record = _PENDING.get(text)
        if not isinstance(record, dict):
            return False
        if record.get("decision") != "once" or record.get("consumed_at_ms"):
            return False
        record["consumed_at_ms"] = _now_ms()
        _persist_pending_locked()
        return True


def register_user_exemption(
    *,
    action: str,
    paths: list[str],
    principal_scope_hash: str = "",
    session_id: str = "",
) -> int:
    """Record this turn's user-specified paths (dynamic, single-turn evidence).

    The evidence is minted exclusively from backend context
    (check_tool_permission + user_message); nothing in the model's tool
    arguments can create it.
    """
    now = _now_ms()
    added = 0
    with _LOCK:
        _EXEMPTIONS[:] = [
            item for item in _EXEMPTIONS if int(item.get("expires_at_ms") or 0) > now
        ]
        for raw in paths or []:
            normalized = normalize_path_text(raw)
            if not normalized:
                continue
            _EXEMPTIONS.append(
                {
                    "action": str(action or ""),
                    "path": normalized,
                    "principal_scope_hash": str(principal_scope_hash or ""),
                    "session_id": str(session_id or ""),
                    "expires_at_ms": now + EXEMPTION_TTL_MS,
                }
            )
            added += 1
    return added


def _exemption_covers_locked(action: str, principal: str, path: str, now: int) -> bool:
    for item in _EXEMPTIONS:
        if int(item.get("expires_at_ms") or 0) <= now:
            continue
        if str(item.get("action") or "") != str(action):
            continue
        if str(item.get("principal_scope_hash") or "") != str(principal):
            continue
        if _paths_match(path, str(item.get("path") or "")):
            return True
    return False


def gateway_path_evidence(
    *,
    action: str,
    principal_scope_hash: str,
    paths: list[str],
    workspace_root: str = "",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Gateway-side lookup: which absolute paths may bypass workspace_only.

    Returns the relaxed path set plus a signed confirmation grant (when one
    covers the request) so the caller can hand it to PolicyEngine.  Anything
    not covered keeps the default fail-closed validation.
    """
    now = _now_ms() if now_ms is None else int(now_ms)
    relaxed: set[str] = set()
    confirmation: dict[str, Any] | None = None
    once_confirm_id = ""
    normalized_paths = [normalize_path_text(item) for item in paths or []]
    normalized_paths = [item for item in normalized_paths if item]
    with _LOCK:
        _load_pending_locked()
        grant_records = _live_grant_records_locked(action, principal_scope_hash, now)
        for record in grant_records:
            bound = str(record.get("target") or "")
            covering = [path for path in normalized_paths if _paths_match(path, bound)]
            if not covering:
                continue
            verification = verify_confirmation_grant(
                record["grant"],
                action=action,
                principal_scope_hash=principal_scope_hash,
                now_ms=now,
            )
            if not verification.get("ok"):
                continue
            relaxed.update(covering)
            if confirmation is None:
                confirmation = record["grant"]
                if record.get("decision") == "once" and not record.get("consumed_at_ms"):
                    once_confirm_id = str(record.get("confirm_id") or "")
        for path in normalized_paths:
            if path in relaxed:
                continue
            if _exemption_covers_locked(action, principal_scope_hash, path, now):
                relaxed.add(path)
    return {
        "relaxed_paths": relaxed,
        "confirmation": confirmation,
        "once_confirm_id": once_confirm_id,
    }


__all__ = [
    "create_pending",
    "resolve",
    "get_pending",
    "list_pending",
    "verify_confirmation_grant",
    "find_live_grant",
    "consume_once_grant",
    "register_user_exemption",
    "gateway_path_evidence",
    "normalize_path_text",
]
