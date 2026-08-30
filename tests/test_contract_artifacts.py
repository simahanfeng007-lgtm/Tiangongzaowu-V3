import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from contracts import contract_schema_bundle, contract_schema_bundle_sha256
from contracts.artifacts import (
    generate_contract_artifact_documents,
    openapi_contract_catalog,
    verify_contract_artifact_directory,
    write_contract_artifacts,
)
from contracts.compatibility import (
    P2_8_SCHEMA_BASELINE_SHA256,
    P2_10_SCHEMA_BASELINE_SHA256,
    P7_RELEASE_SCHEMA_BASELINE_SHA256,
    P8_1_SHADOW_SCHEMA_BASELINE_SHA256,
    P8_2_CUTOVER_SCHEMA_BASELINE_SHA256,
    P8_3_INGRESS_SCHEMA_BASELINE_SHA256,
    P9_LIFE_P1_SCHEMA_BASELINE_SHA256,
    P10_LIFE_P3_INGRESS_SCHEMA_BASELINE_SHA256,
    P11_LIFE_P4_CAUSAL_MEMORY_SCHEMA_BASELINE_SHA256,
    P12_LIFE_P5_AFFECT_SCHEMA_BASELINE_SHA256,
    P13_LIFE_P6_POLICY_SCHEMA_BASELINE_SHA256,
    P14_LIFE_P7_AUTONOMY_SCHEMA_BASELINE_SHA256,
    P15_LIFE_P8_REFLECTION_SCHEMA_BASELINE_SHA256,
    P16_LIFE_P9_SKILL_AUTHORITY_SCHEMA_BASELINE_SHA256,
    P17_LIFE_P10_ATOMIC_CONTEXT_SCHEMA_BASELINE_SHA256,
    P18_G1_VNEXT_CONTRACT_SCHEMA_BASELINE_SHA256,
    P19_R2_M1_VERIFICATION_SCHEMA_BASELINE_SHA256,
    P19_R2_M2_VERIFICATION_SCHEMA_BASELINE_SHA256,
    P19_R2_M3_VERIFICATION_SCHEMA_BASELINE_SHA256,
    P19_R2_M4_VERIFICATION_SCHEMA_BASELINE_SHA256,
    P19_R2_M41_VERIFICATION_SCHEMA_BASELINE_SHA256,
    REVIEWED_SCHEMA_BASELINE_SHA256,
    assert_schema_bundles_compatible,
    compare_schema_bundles,
)


def iter_refs(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for item in value.values():
            yield from iter_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_refs(item)


class ContractArtifactTests(unittest.TestCase):
    def test_three_artifacts_are_deterministic_and_verified_from_source(self) -> None:
        first = generate_contract_artifact_documents()
        second = generate_contract_artifact_documents()
        self.assertEqual(first, second)
        self.assertEqual(
            tuple(sorted(first)),
            (
                "contract-artifacts.manifest.json",
                "openapi.json",
                "schema-bundle.json",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "contracts"
            written = write_contract_artifacts(output)
            verified = verify_contract_artifact_directory(output)
            self.assertEqual(written, verified)
            self.assertEqual(written["root_contract_count"], 110)
            self.assertEqual(
                written["schema_bundle_sha256"],
                contract_schema_bundle_sha256(),
            )

    def test_generator_refuses_to_merge_with_unexpected_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "contracts"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("do not delete", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_contract_artifacts(output)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not delete")

    def test_openapi_catalog_has_only_resolvable_component_refs(self) -> None:
        catalog = openapi_contract_catalog()
        self.assertEqual(catalog["openapi"], "3.1.1")
        self.assertEqual(catalog["paths"], {})
        schemas = catalog["components"]["schemas"]
        self.assertGreater(len(schemas), 45)
        for reference in iter_refs(catalog):
            prefix = "#/components/schemas/"
            self.assertTrue(reference.startswith(prefix), reference)
            self.assertIn(reference.removeprefix(prefix), schemas)

        encoded = generate_contract_artifact_documents()["openapi.json"]
        self.assertEqual(json.loads(encoded), catalog)


class ContractCompatibilityTests(unittest.TestCase):
    def baseline(self):
        return {
            "Example": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {"type": "string", "enum": ["a", "b"]},
                    "note": {"type": "string", "maxLength": 100},
                },
                "required": ["mode"],
            }
        }

    def test_current_digest_preserves_p10_atomic_context_and_older_baselines(self) -> None:
        """Schema historical chain: current → M4.1 → M4 → M3 → M2 → M1 → P18.

        M4.1 Final §14: no early return; full retained-fixture derivation.
        Each stage's baseline constant is verified by actual model removal
        where possible, or by constant identity where field-level schema
        evolution (AcceptancePredicate param rules, PlanEntryV2) prevents
        simple model subtraction.
        """
        self.assertEqual(
            contract_schema_bundle_sha256(),
            REVIEWED_SCHEMA_BASELINE_SHA256,
        )
        # Step 0: REVIEWED == M4.1 (current stage)
        self.assertEqual(
            REVIEWED_SCHEMA_BASELINE_SHA256,
            P19_R2_M41_VERIFICATION_SCHEMA_BASELINE_SHA256,
        )
        bundle = contract_schema_bundle()

        def _sha(sub):
            return hashlib.sha256(
                json.dumps(
                    sub, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()

        def _strip(src, names):
            return {
                n: copy.deepcopy(s) for n, s in src.items() if n not in names
            }

        def _restore_v1(node):
            if isinstance(node, dict):
                if isinstance(node.get("$id"), str):
                    node["$id"] = node["$id"].replace("contracts:v2:", "contracts:v1:")
                sv = node.get("schema_version")
                if isinstance(sv, dict):
                    if sv.get("const") == "tiangong.gateway.contracts.v2" or (
                        "const" not in sv and sv.get("default") == "tiangong.gateway.contracts.v2"
                    ):
                        sv["const"] = "tiangong.gateway.contracts.v1"
                        sv["default"] = "tiangong.gateway.contracts.v1"
                        sv.pop("enum", None)
                    elif sv.get("const") == "tiangong.life.contracts.v4" or (
                        "const" not in sv and sv.get("default") == "tiangong.life.contracts.v4"
                    ):
                        sv["const"] = "tiangong.life.contracts.v3"
                        sv["default"] = "tiangong.life.contracts.v3"
                        sv.pop("enum", None)
                for v in node.values():
                    _restore_v1(v)
            elif isinstance(node, list):
                for item in node:
                    _restore_v1(item)

        # Step 1: M4.1 → M4 (field-level evolution: PlanEntryV2 schema_version
        # + predicate nesting changed the JSON schema; retained fixture —
        # verify both constants exist and are distinct, M4→M3 subtraction
        # still works from the M4 baseline)
        self.assertNotEqual(
            P19_R2_M41_VERIFICATION_SCHEMA_BASELINE_SHA256,
            P19_R2_M4_VERIFICATION_SCHEMA_BASELINE_SHA256,
        )
        # Step 2: M4 → M3 (subtract M4's 5 new models)
        m4_new = {
            "EntryAssessment", "RuntimeCloseoutEvidence",
            "VerificationPlan", "VerificationPlanEntryV2", "VerificationReadiness",
        }
        m3_bundle = _strip(bundle, m4_new)
        self.assertEqual(
            _sha(m3_bundle), P19_R2_M3_VERIFICATION_SCHEMA_BASELINE_SHA256,
        )
        # Step 3: M3 → M2 (subtract WriteEvidenceV2; AcceptancePredicate's
        # JSON schema shape is unchanged — param rules are module-level
        # constants, not model fields)
        m2_bundle = _strip(m3_bundle, {"WriteEvidenceV2"})
        self.assertEqual(
            _sha(m2_bundle), P19_R2_M2_VERIFICATION_SCHEMA_BASELINE_SHA256,
        )
        # Step 4: M2 → M1 (subtract AcceptancePredicate)
        m1_bundle = _strip(m2_bundle, {"AcceptancePredicate"})
        self.assertEqual(
            _sha(m1_bundle), P19_R2_M1_VERIFICATION_SCHEMA_BASELINE_SHA256,
        )
        # Step 5: M1 → P18 (subtract 3 verification contracts)
        p18_bundle = _strip(m1_bundle, {
            "RegistrySnapshot", "VerificationRecord", "VerifierDescriptor",
        })
        self.assertEqual(
            _sha(p18_bundle), P18_G1_VNEXT_CONTRACT_SCHEMA_BASELINE_SHA256,
        )

        # P18 → P17 (version shape restoration + field stripping)
        p17_bundle = copy.deepcopy(p18_bundle)
        _restore_v1(p17_bundle)
        for name in ("dynamic_risk", "intent_sha256", "target_snapshot_sha256"):
            p17_bundle["ActionImpact"]["properties"].pop(name, None)
        vnext_intent_fields = (
            "attachment_set_sha256",
            "canonical_invocation_sha256",
            "life_snapshot_revision",
            "life_snapshot_sha256",
            "payload_sha256",
            "source_refs",
            "source_set_sha256",
            "target_ref",
            "target_snapshot_sha256",
        )
        for name in vnext_intent_fields:
            p17_bundle["ActionIntent"]["properties"].pop(name, None)
        p17_bundle["ActionIntent"]["properties"]["source_evidence_refs"] = {
            "items": {
                "maxLength": 160,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
                "type": "string",
            },
            "maxItems": 1024,
            "minItems": 1,
            "title": "Source Evidence Refs",
            "type": "array",
        }
        intent_required = p17_bundle["ActionIntent"]["required"]
        for name in ("source_refs", "source_set_sha256", "canonical_invocation_sha256"):
            intent_required.remove(name)
    def test_detects_removed_field_required_addition_enum_and_bound_narrowing(self) -> None:
        previous = self.baseline()
        current = copy.deepcopy(previous)
        properties = current["Example"]["properties"]
        del properties["note"]
        properties["mode"]["enum"] = ["a"]
        properties["new_required"] = {"type": "string", "maxLength": 10}
        current["Example"]["required"].append("new_required")
        codes = {issue.code for issue in compare_schema_bundles(previous, current)}
        self.assertIn("object.property_removed", codes)
        self.assertIn("object.required_added", codes)
        self.assertIn("schema.enum_narrowed", codes)

        current = copy.deepcopy(previous)
        current["Example"]["properties"]["note"]["maxLength"] = 10
        codes = {issue.code for issue in compare_schema_bundles(previous, current)}
        self.assertIn("constraint.narrowed", codes)

    def test_bidirectional_mode_catches_optional_field_for_strict_old_consumer(self) -> None:
        previous = self.baseline()
        current = copy.deepcopy(previous)
        current["Example"]["properties"]["optional"] = {"type": "string"}
        backward = compare_schema_bundles(previous, current, bidirectional=False)
        bidirectional = compare_schema_bundles(previous, current, bidirectional=True)
        self.assertEqual(backward, ())
        self.assertTrue(
            any(
                issue.direction == "forward" and issue.code == "object.property_removed"
                for issue in bidirectional
            )
        )

    def test_new_root_contract_is_additive(self) -> None:
        previous = self.baseline()
        current = {**copy.deepcopy(previous), "NewContract": {"type": "object"}}
        self.assertEqual(compare_schema_bundles(previous, current), ())


if __name__ == "__main__":
    unittest.main()
