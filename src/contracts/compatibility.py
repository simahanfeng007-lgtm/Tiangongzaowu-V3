"""Conservative compatibility checks for generated JSON Schema bundles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any, Literal

P2_8_SCHEMA_BASELINE_SHA256 = (
    "9be303575eb4c19f73437c285ed675201af5c984909d58382698c35e810d1802"
)
P2_10_SCHEMA_BASELINE_SHA256 = (
    "6b08c98b9ba6f0020e6f3549e4d93aea439080eade63e51e125f092544915f1a"
)
P7_RELEASE_SCHEMA_BASELINE_SHA256 = (
    "909f06082379828d109a7d5537efb2c7c8ecd5997b521d4b73b9774237a58287"
)
P8_1_SHADOW_SCHEMA_BASELINE_SHA256 = (
    "fe832c7b72ec854682802452552a1263d896e34405b5358c9285d8cffd2e0d68"
)
P8_2_CUTOVER_SCHEMA_BASELINE_SHA256 = (
    "5a749ea51e903e9e5cf571943316e8c314cdd543d95e456cb5cc41c3f6057493"
)
P8_3_INGRESS_SCHEMA_BASELINE_SHA256 = (
    "ec90544efacf6f4434a17b42e9015a7365434fae8d449445c1f6ad54ff05e194"
)
P9_LIFE_P1_SCHEMA_BASELINE_SHA256 = (
    "71358326e2fd115346fe9e1a1a2e59dffbcfdafa6a8b88b90f126521b45f248e"
)
P10_LIFE_P3_INGRESS_SCHEMA_BASELINE_SHA256 = (
    "0aed86b268235b57fb6c4127e354e5d3f6f900ebb1a30377eb20021f6b475678"
)
P11_LIFE_P4_CAUSAL_MEMORY_SCHEMA_BASELINE_SHA256 = (
    "cd2c2e142d75637b178cd3c2cc7d8440db7d923b44c0fb37a8cc7d492fd28343"
)
P12_LIFE_P5_AFFECT_SCHEMA_BASELINE_SHA256 = (
    "1a35923dd6d2318142212cb423ac1364e16859b58dcff973e303f26bc2485a42"
)
P13_LIFE_P6_POLICY_SCHEMA_BASELINE_SHA256 = (
    "a13b5fca4bb6925e9829416ff1c913ae813483a3fbe00a7445dd3fb85f352ec7"
)
P14_LIFE_P7_AUTONOMY_SCHEMA_BASELINE_SHA256 = (
    "4d16ad4e6364bc301b01554514f1a610507c8bacde24eee376fe26f7b60ff10a"
)
P15_LIFE_P8_REFLECTION_SCHEMA_BASELINE_SHA256 = (
    "9e9f02d464b87f7ad7ee14dd7119c011802535c425a98f5350f91ded836a1a35"
)
P16_LIFE_P9_SKILL_AUTHORITY_SCHEMA_BASELINE_SHA256 = (
    "d1ff912e165d67757cc436e1bd3afd183f8ac275f2290a2039522d133b7cb420"
)
P17_LIFE_P10_ATOMIC_CONTEXT_SCHEMA_BASELINE_SHA256 = (
    "0b396d2526c20002a978a18b4699d19ee2b80ae568e27f11a87d350db5902bb6"
)
# G1 合同 vNext（schema v1→v2 + provenance/claim/fence 绑定）后的当前包 digest。
P18_G1_VNEXT_CONTRACT_SCHEMA_BASELINE_SHA256 = (
    "09e24fbd4d36333dd63b62f10af1fef7316a4263513b9b539ce598b69fbd875c"
)
# P19-R2 M1 验证平面合同（VerifierDescriptor/RegistrySnapshot/VerificationRecord）阶段的包 digest。
P19_R2_M1_VERIFICATION_SCHEMA_BASELINE_SHA256 = (
    "502be945ad687f75a3eb085cdd569fb50aaef3d3876e11993fff79cde73b0965"
)
# P19-R2 M2.1 起 AcceptancePredicate 加入合同包后的当前阶段 digest。
# JSON Schema 形状在 M2.2 未变（M2.2 只改验证器语义与边界行为）。
P19_R2_M2_VERIFICATION_SCHEMA_BASELINE_SHA256 = (
    "d46cd149db4cc37883270f4e91e5592dc2dc2b34765283b5759f680f8ad1609a"
)
REVIEWED_SCHEMA_BASELINE_SHA256 = P19_R2_M2_VERIFICATION_SCHEMA_BASELINE_SHA256
CompatibilityDirection = Literal["backward", "forward"]


@dataclass(frozen=True, order=True)
class CompatibilityIssue:
    direction: CompatibilityDirection
    code: str
    path: str
    detail: str


class ContractCompatibilityError(ValueError):
    def __init__(self, issues: Sequence[CompatibilityIssue]) -> None:
        ordered = tuple(sorted(set(issues)))
        summary = "; ".join(
            f"{issue.direction}:{issue.code}:{issue.path}" for issue in ordered
        )
        super().__init__(summary)
        self.issues = ordered


def _json_identity(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return value
    return None


def _append(
    issues: list[CompatibilityIssue],
    direction: CompatibilityDirection,
    code: str,
    path: str,
    detail: str,
) -> None:
    issues.append(CompatibilityIssue(direction, code, path or "/", detail))


def _compare_bound(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    keyword: str,
    *,
    lower: bool,
    path: str,
    direction: CompatibilityDirection,
    issues: list[CompatibilityIssue],
) -> None:
    if keyword not in current:
        return
    current_value = current[keyword]
    previous_value = previous.get(keyword)
    if not isinstance(current_value, int) or isinstance(current_value, bool):
        _append(issues, direction, "schema.invalid_bound", path, keyword)
        return
    narrowed = previous_value is None
    if isinstance(previous_value, int) and not isinstance(previous_value, bool):
        narrowed = current_value > previous_value if lower else current_value < previous_value
    if narrowed:
        _append(issues, direction, "constraint.narrowed", path, keyword)


def _compare_alternatives(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    keyword: str,
    *,
    path: str,
    direction: CompatibilityDirection,
    issues: list[CompatibilityIssue],
) -> None:
    if keyword not in current:
        return
    current_items = current[keyword]
    previous_items = previous.get(keyword)
    if not isinstance(current_items, list):
        _append(issues, direction, "schema.invalid_alternatives", path, keyword)
        return
    if previous_items is None:
        _append(issues, direction, "constraint.added", path, keyword)
        return
    if not isinstance(previous_items, list):
        _append(issues, direction, "schema.alternatives_changed", path, keyword)
        return
    current_set = {_json_identity(item) for item in current_items}
    if any(_json_identity(item) not in current_set for item in previous_items):
        _append(issues, direction, "schema.alternative_removed", path, keyword)


def _compare_node(
    previous: object,
    current: object,
    *,
    path: str,
    direction: CompatibilityDirection,
    issues: list[CompatibilityIssue],
) -> None:
    old = _mapping(previous)
    new = _mapping(current)
    if old is None or new is None:
        if _json_identity(previous) != _json_identity(current):
            _append(issues, direction, "schema.shape_changed", path, "non-object schema")
        return

    if "$ref" in old or "$ref" in new:
        if old.get("$ref") != new.get("$ref"):
            _append(issues, direction, "schema.ref_changed", path, "$ref")
        return

    if old.get("type") != new.get("type"):
        _append(issues, direction, "schema.type_changed", path, "type")

    if "const" in new and old.get("const", object()) != new["const"]:
        _append(issues, direction, "schema.const_narrowed", path, "const")

    if "enum" in new:
        new_enum = new["enum"]
        old_enum = old.get("enum")
        if not isinstance(new_enum, list):
            _append(issues, direction, "schema.invalid_enum", path, "enum")
        elif old_enum is None:
            _append(issues, direction, "schema.enum_added", path, "enum")
        elif not isinstance(old_enum, list) or any(item not in new_enum for item in old_enum):
            _append(issues, direction, "schema.enum_narrowed", path, "enum")

    old_required = set(old.get("required", ()))
    new_required = set(new.get("required", ()))
    for name in sorted(new_required - old_required):
        _append(issues, direction, "object.required_added", f"{path}/properties/{name}", name)

    old_properties = _mapping(old.get("properties", {})) or {}
    new_properties = _mapping(new.get("properties", {})) or {}
    for name in sorted(old_properties):
        child_path = f"{path}/properties/{name}"
        if name not in new_properties:
            _append(issues, direction, "object.property_removed", child_path, name)
            continue
        _compare_node(
            old_properties[name],
            new_properties[name],
            path=child_path,
            direction=direction,
            issues=issues,
        )

    old_defs = _mapping(old.get("$defs", {})) or {}
    new_defs = _mapping(new.get("$defs", {})) or {}
    for name in sorted(old_defs):
        child_path = f"{path}/$defs/{name}"
        if name not in new_defs:
            _append(issues, direction, "schema.definition_removed", child_path, name)
            continue
        _compare_node(
            old_defs[name],
            new_defs[name],
            path=child_path,
            direction=direction,
            issues=issues,
        )

    old_additional = old.get("additionalProperties", True)
    new_additional = new.get("additionalProperties", True)
    if old_additional is not False and new_additional is False:
        _append(
            issues,
            direction,
            "object.additional_properties_forbidden",
            path,
            "additionalProperties",
        )
    elif _mapping(old_additional) is not None and _mapping(new_additional) is not None:
        _compare_node(
            old_additional,
            new_additional,
            path=f"{path}/additionalProperties",
            direction=direction,
            issues=issues,
        )

    for keyword in ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties"):
        _compare_bound(
            old,
            new,
            keyword,
            lower=True,
            path=path,
            direction=direction,
            issues=issues,
        )
    for keyword in ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties"):
        _compare_bound(
            old,
            new,
            keyword,
            lower=False,
            path=path,
            direction=direction,
            issues=issues,
        )

    for keyword in ("pattern", "format", "multipleOf", "uniqueItems", "not"):
        if keyword in new and old.get(keyword, object()) != new[keyword]:
            _append(issues, direction, "constraint.changed", path, keyword)

    if "items" in new:
        if "items" not in old:
            _append(issues, direction, "array.items_restricted", path, "items")
        else:
            _compare_node(
                old["items"],
                new["items"],
                path=f"{path}/items",
                direction=direction,
                issues=issues,
            )

    for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
        _compare_alternatives(
            old,
            new,
            keyword,
            path=path,
            direction=direction,
            issues=issues,
        )


def compare_schema_bundles(
    previous: Mapping[str, object],
    current: Mapping[str, object],
    *,
    bidirectional: bool = True,
) -> tuple[CompatibilityIssue, ...]:
    """Return conservative issues; new root contracts are allowed."""

    if any(not isinstance(name, str) for name in (*previous.keys(), *current.keys())):
        raise TypeError("schema bundle names must be strings")
    issues: list[CompatibilityIssue] = []
    for name in sorted(previous):
        path = f"/{name}"
        if name not in current:
            _append(issues, "backward", "schema.removed", path, name)
            continue
        _compare_node(
            previous[name],
            current[name],
            path=path,
            direction="backward",
            issues=issues,
        )
        if bidirectional:
            _compare_node(
                current[name],
                previous[name],
                path=path,
                direction="forward",
                issues=issues,
            )
    return tuple(sorted(set(issues)))


def assert_schema_bundles_compatible(
    previous: Mapping[str, object],
    current: Mapping[str, object],
    *,
    bidirectional: bool = True,
) -> None:
    issues = compare_schema_bundles(previous, current, bidirectional=bidirectional)
    if issues:
        raise ContractCompatibilityError(issues)


__all__ = [
    "CompatibilityIssue",
    "ContractCompatibilityError",
    "P2_8_SCHEMA_BASELINE_SHA256",
    "P2_10_SCHEMA_BASELINE_SHA256",
    "P7_RELEASE_SCHEMA_BASELINE_SHA256",
    "P8_1_SHADOW_SCHEMA_BASELINE_SHA256",
    "P8_2_CUTOVER_SCHEMA_BASELINE_SHA256",
    "P8_3_INGRESS_SCHEMA_BASELINE_SHA256",
    "P9_LIFE_P1_SCHEMA_BASELINE_SHA256",
    "P10_LIFE_P3_INGRESS_SCHEMA_BASELINE_SHA256",
    "P11_LIFE_P4_CAUSAL_MEMORY_SCHEMA_BASELINE_SHA256",
    "P12_LIFE_P5_AFFECT_SCHEMA_BASELINE_SHA256",
    "P13_LIFE_P6_POLICY_SCHEMA_BASELINE_SHA256",
    "P14_LIFE_P7_AUTONOMY_SCHEMA_BASELINE_SHA256",
    "P15_LIFE_P8_REFLECTION_SCHEMA_BASELINE_SHA256",
    "P16_LIFE_P9_SKILL_AUTHORITY_SCHEMA_BASELINE_SHA256",
    "P17_LIFE_P10_ATOMIC_CONTEXT_SCHEMA_BASELINE_SHA256",
    "REVIEWED_SCHEMA_BASELINE_SHA256",
    "assert_schema_bundles_compatible",
    "compare_schema_bundles",
]
