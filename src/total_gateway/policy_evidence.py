"""Append-only, content-addressed evidence for gateway policy authority.

Policy objects are facts, not transient log lines. Each canonical contract is
stored independently and every evaluation writes a linking manifest. Existing
content addresses are verified byte-for-byte; collisions or drift fail closed.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Mapping

from contracts import canonical_json_bytes, canonical_sha256


_KIND = re.compile(r"^[a-z][a-z0-9._-]{0,79}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
# Physical directories are deliberately compact: the long logical kind is
# retained in every returned evidence reference, while Windows receives a
# stable one-character namespace below the short ``p`` evidence root.
_KIND_DIRECTORY = {
    "life-event": "l",
    "action-intent": "i",
    "action-impact": "m",
    "action-permission": "p",
    "action-registry": "r",
    "policy-decision": "d",
    "execution-ticket": "t",
    "omni-capability-grant": "g",
    "evaluation": "e",
}


class PolicyEvidenceError(RuntimeError):
    pass


class PolicyEvidenceLedger:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("policy evidence root is unsafe")
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise PolicyEvidenceError("policy evidence root is unsafe")
        self._lock = threading.RLock()

    @staticmethod
    def _payload(value: Any) -> Mapping[str, Any]:
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
        elif isinstance(value, Mapping):
            dumped = dict(value)
        else:
            raise TypeError("policy evidence must be a contract or mapping")
        if not isinstance(dumped, Mapping):
            raise TypeError("policy evidence root must be an object")
        return dumped

    def record(self, kind: str, object_id: str, value: Any) -> dict[str, str]:
        if not _KIND.fullmatch(kind) or not _ID.fullmatch(object_id):
            raise ValueError("policy evidence identity is invalid")
        payload = self._payload(value)
        digest = canonical_sha256(payload)
        body = canonical_json_bytes(payload) + b"\n"
        directory_name = _KIND_DIRECTORY.get(kind)
        if directory_name is None:
            raise ValueError("policy evidence kind is not registered")
        directory = self.root / directory_name
        path = directory / f"{digest}.json"
        with self._lock:
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink():
                raise PolicyEvidenceError("policy evidence directory is unsafe")
            if path.exists():
                if path.is_symlink() or path.read_bytes() != body:
                    raise PolicyEvidenceError("policy evidence content-address conflict")
            else:
                # Evidence roots can live below a long per-life data path on
                # Windows.  Keep the atomic staging leaf deliberately short;
                # the final content-addressed name remains the authority.
                temp = directory / ("~" + secrets.token_hex(8) + ".tmp")
                try:
                    with temp.open("xb") as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp, path)
                finally:
                    temp.unlink(missing_ok=True)
        return {"kind": kind, "object_id": object_id, "sha256": digest, "path": str(path)}

    def record_evaluation(
        self,
        *,
        intent: Any,
        impact: Any,
        permission: Any,
        registry: Any,
        decision: Any,
        ticket: Any | None,
        grant: Any | None,
        observed_at_ms: int,
    ) -> dict[str, Any]:
        rows = [
            self.record("action-intent", str(intent.intent_id), intent),
            self.record("action-impact", str(impact.impact_id), impact),
            self.record("action-permission", str(permission.action_id), permission),
            self.record("action-registry", str(registry.registry_id), registry),
            self.record("policy-decision", str(decision.decision_id), decision),
        ]
        if ticket is not None:
            rows.append(self.record("execution-ticket", str(ticket.payload.ticket_id), ticket))
        if grant is not None:
            rows.append(self.record("omni-capability-grant", str(grant.payload.grant_id), grant))
        manifest_seed = {
            "schema": "tiangong.gateway.policy-evaluation-fact.v1",
            "request_id": str(intent.request_id),
            "run_id": str(intent.run_id),
            "generation": int(intent.generation),
            "observed_at_ms": int(observed_at_ms),
            "objects": sorted(rows, key=lambda row: (row["kind"], row["object_id"])),
        }
        manifest_id = "policy-evaluation-" + canonical_sha256(manifest_seed)
        manifest = {**manifest_seed, "evaluation_id": manifest_id}
        manifest_row = self.record("evaluation", manifest_id, manifest)
        return {"evaluation": manifest_row, "objects": rows}


__all__ = ["PolicyEvidenceError", "PolicyEvidenceLedger"]
