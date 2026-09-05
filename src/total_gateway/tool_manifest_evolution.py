"""Gateway-owned read-only review through the existing permission compiler.

This is a differential observation, not a registry, validator implementation,
approval, sandbox receipt or Source publication. Even a report with no visible
manifest changes says nothing about the safety of changed handler source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from typing import Any, Mapping

from contracts import ActionPermission, canonical_json_bytes, canonical_sha256
from total_gateway.action_registry import LoadedActionAuthority, compile_action_authority


class ManifestEvolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActionManifestDeltaV1:
    action_id: str
    kind: str
    changed_fields: tuple[str, ...]
    permission_changed_fields: tuple[str, ...]
    before_capability_sha256: str | None
    after_capability_sha256: str | None
    before_permission_sha256: str | None
    after_permission_sha256: str | None
    before_effective_risk: str | None
    after_effective_risk: str | None
    before_canonical_action_id: str | None
    after_canonical_action_id: str | None
    review_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManifestEvolutionReviewV1:
    schema: str
    base_manifest_sha256: str
    candidate_manifest_sha256: str
    manifest_changed_fields: tuple[str, ...]
    requested_action_ids: tuple[str, ...]
    deltas: tuple[ActionManifestDeltaV1, ...]
    unexpected_action_ids: tuple[str, ...]
    requested_without_manifest_delta: tuple[str, ...]
    newly_executable_action_ids: tuple[str, ...]
    risk_downgraded_action_ids: tuple[str, ...]
    newly_a0_action_ids: tuple[str, ...]
    review_sha256: str
    may_authorize: bool = False
    may_execute: bool = False
    may_publish: bool = False

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("review_sha256")
        return value

    def has_valid_sha256(self) -> bool:
        return (
            self.schema == "tiangong.tool-manifest-evolution-review.v1"
            and self.may_authorize is False
            and self.may_execute is False
            and self.may_publish is False
            and self.review_sha256 == canonical_sha256(self.payload())
        )


def _authority(manifest: Mapping[str, Any]) -> LoadedActionAuthority:
    # Detach caller-owned nested mappings before computing any projection.
    try:
        detached = json.loads(canonical_json_bytes(dict(manifest)))
    except (TypeError, ValueError) as exc:
        raise ManifestEvolutionError("manifest is not canonical JSON") from exc
    rows = detached.get("capabilities")
    if not isinstance(rows, dict) or any(
        not isinstance(row, dict) or row.get("id") != action_id
        or type(row.get("executable")) is not bool
        for action_id, row in rows.items()
    ):
        raise ManifestEvolutionError("manifest has an invalid action row")
    unavailable = detached.get("unavailable")
    if type(unavailable) is not int or unavailable != sum(
        row["executable"] is False for row in rows.values()
    ):
        raise ManifestEvolutionError("manifest unavailable count is stale")
    # This compiler remains the only source of permission floors, alias risk,
    # path policy and argument/result/value schema authority.
    return compile_action_authority(detached, generated_at_ms=0)


def _permission_body(permission: ActionPermission | None) -> dict[str, Any] | None:
    if permission is None:
        return None
    # Whole-manifest identity changes every permission's sealed hash. Compare
    # actual permission semantics, not that transitive hash churn.
    return permission.model_dump(
        mode="json", exclude={"source_manifest_sha256", "permission_sha256"}
    )


def _changed(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> tuple[str, ...]:
    left, right = before or {}, after or {}
    return tuple(sorted(
        key for key in left.keys() | right.keys()
        if key not in left or key not in right
        or canonical_json_bytes(left[key]) != canonical_json_bytes(right[key])
    ))


def _digest(value: Mapping[str, Any] | None) -> str | None:
    return canonical_sha256(dict(value)) if value is not None else None


def review_manifest_evolution(
    base_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
    *,
    requested_action_ids: tuple[str, ...],
) -> ManifestEvolutionReviewV1:
    """Enumerate all direct AND alias-inherited changes without approval.

    Both manifest projections must pass the existing Gateway compiler. Scope
    mismatches are reported in full so a reviewer can see collateral changes;
    they are never converted into an implicit approval. A source-only handler
    change can legitimately have no manifest delta and still requires source
    review, isolated build/tests, and the publication lifecycle.
    """
    if (
        not isinstance(requested_action_ids, tuple)
        or any(not isinstance(item, str) or not item for item in requested_action_ids)
        or requested_action_ids != tuple(sorted(set(requested_action_ids)))
    ):
        raise ManifestEvolutionError("requested actions must be sorted and unique")
    before, after = _authority(base_manifest), _authority(candidate_manifest)
    before_rows, after_rows = before.manifest["capabilities"], after.manifest["capabilities"]
    all_ids = before_rows.keys() | after_rows.keys()
    if not set(requested_action_ids) <= all_ids:
        raise ManifestEvolutionError("requested action is absent from both manifests")
    before_permissions = {item.action_id: item for item in before.registry.permissions}
    after_permissions = {item.action_id: item for item in after.registry.permissions}
    before_schemas = {item.action_id: item for item in before.schema_catalog.entries}
    after_schemas = {item.action_id: item for item in after.schema_catalog.entries}
    deltas: list[ActionManifestDeltaV1] = []
    newly_executable, risk_downgraded, newly_a0 = [], [], []
    for action_id in sorted(all_ids):
        left, right = before_rows.get(action_id), after_rows.get(action_id)
        old_permission, new_permission = before_permissions.get(action_id), after_permissions.get(action_id)
        old_body, new_body = _permission_body(old_permission), _permission_body(new_permission)
        fields, permission_fields = _changed(left, right), _changed(old_body, new_body)
        old_schema, new_schema = before_schemas.get(action_id), after_schemas.get(action_id)
        old_canonical = old_schema.canonical_action_id if old_schema else None
        new_canonical = new_schema.canonical_action_id if new_schema else None
        if not fields and not permission_fields and old_canonical == new_canonical:
            continue
        reasons: set[str] = {"SOURCE_AND_CONTRACT_REVIEW_REQUIRED"}
        if permission_fields:
            reasons.add("PERMISSION_SEMANTICS_CHANGED")
        if new_permission is not None and old_permission is None:
            newly_executable.append(action_id)
            reasons.add("NEW_EXECUTABLE_ACTION")
        if old_permission is not None and new_permission is None:
            reasons.add("EXECUTABLE_ACTION_REMOVED")
        if old_permission is not None and new_permission is not None:
            if int(new_permission.effective_risk[1:]) < int(old_permission.effective_risk[1:]):
                risk_downgraded.append(action_id)
                reasons.add("EFFECTIVE_RISK_DOWNGRADED")
        if new_permission is not None and new_permission.effective_risk == "A0" and (
            old_permission is None or old_permission.effective_risk != "A0"
        ):
            newly_a0.append(action_id)
            reasons.add("NEW_A0_ADMISSION_CANDIDATE")
        if any("schema" in name or "validator" in name for name in fields):
            reasons.add("SCHEMA_OR_VALIDATOR_CHANGED")
        if old_canonical != new_canonical:
            reasons.add("CANONICAL_ACTION_CHANGED")
        deltas.append(ActionManifestDeltaV1(
            action_id=action_id,
            kind="ADDED" if left is None else "REMOVED" if right is None else "MODIFIED",
            changed_fields=fields,
            permission_changed_fields=permission_fields,
            before_capability_sha256=_digest(left),
            after_capability_sha256=_digest(right),
            before_permission_sha256=old_permission.permission_sha256 if old_permission else None,
            after_permission_sha256=new_permission.permission_sha256 if new_permission else None,
            before_effective_risk=old_permission.effective_risk if old_permission else None,
            after_effective_risk=new_permission.effective_risk if new_permission else None,
            before_canonical_action_id=old_canonical,
            after_canonical_action_id=new_canonical,
            review_reasons=tuple(sorted(reasons)),
        ))
    changed_ids = {delta.action_id for delta in deltas}
    requested = set(requested_action_ids)
    draft = ManifestEvolutionReviewV1(
        schema="tiangong.tool-manifest-evolution-review.v1",
        base_manifest_sha256=before.manifest_sha256,
        candidate_manifest_sha256=after.manifest_sha256,
        manifest_changed_fields=_changed(before.manifest, after.manifest),
        requested_action_ids=requested_action_ids,
        deltas=tuple(deltas),
        unexpected_action_ids=tuple(sorted(changed_ids - requested)),
        requested_without_manifest_delta=tuple(sorted(requested - changed_ids)),
        newly_executable_action_ids=tuple(newly_executable),
        risk_downgraded_action_ids=tuple(risk_downgraded),
        newly_a0_action_ids=tuple(newly_a0),
        review_sha256="0" * 64,
    )
    return replace(draft, review_sha256=canonical_sha256(draft.payload()))
