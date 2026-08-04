import unittest

from pydantic import ValidationError

from contracts import (
    ComponentManifest,
    ComponentReadinessEvidence,
    ReadinessExpectation,
    evaluate_readiness_contract,
    readiness_expectation_from_manifest,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64

COMPONENTS = (
    ("tiangong-backend", "execution", "backend/tiangong-backend.exe", 7174),
    (
        "tiangong-communication-service",
        "communication",
        "communication/tiangong-communication-service.exe",
        7176,
    ),
    ("tiangong-desktop", "desktop", "desktop/tiangong.exe", 0),
    ("tiangong-life-service", "life", "life/tiangong-life-service.exe", 7175),
    ("tiangong-total-gateway", "orchestrator", "gateway/tiangong-total-gateway.exe", 7184),
)


def component_manifest(**overrides):
    components = tuple(
        {
            "component_id": component_id,
            "version": "3.0.0",
            "build_id": f"build_{component_id}",
            "role": role,
            "executable_relative_path": path,
            "sha256": chr(ord("1") + index) * 64,
            "size_bytes": 10_000 + index,
            "ports": () if not port else (port,),
            "api_contract_ids": ("tiangong.gateway.contracts.v1",),
            "schema_bundle_hash": HASH_A,
        }
        for index, (component_id, role, path, port) in enumerate(COMPONENTS)
    )
    values = {
        "manifest_id": "component_manifest_001",
        "product_version": "3.0.0",
        "generated_at_ms": 5_000,
        "contract_schema_bundle_hash": HASH_A,
        "capability_manifest_hash": HASH_B,
        "skill_index_hash": HASH_C,
        "release_policy_hash": HASH_D,
        "components": components,
        "production_claim": True,
        "manifest_sha256": HASH_F,
    }
    values.update(overrides)
    return ComponentManifest(**values).with_computed_manifest_sha256()


def expectation():
    return readiness_expectation_from_manifest(
        component_manifest(),
        expectation_id="readiness_expectation_001",
        gateway_epoch=7,
        contract_artifact_manifest_sha256=HASH_E,
    )


def evidence_for(expected, **overrides):
    values = {
        "evidence_id": f"evidence_{expected.component_id}",
        "component_id": expected.component_id,
        "component_role": expected.role,
        "instance_id": f"instance_{expected.component_id}",
        "version": expected.version,
        "build_id": expected.build_id,
        "executable_sha256": expected.executable_sha256,
        "gateway_epoch": 7,
        "component_manifest_sha256": expectation().component_manifest_sha256,
        "schema_bundle_sha256": HASH_A,
        "capability_manifest_sha256": HASH_B,
        "skill_index_sha256": HASH_C,
        "release_policy_sha256": HASH_D,
        "contract_artifact_manifest_sha256": HASH_E,
        "health_check_passed": True,
        "observed_at_ms": 10_000,
        "evidence_sha256": HASH_F,
    }
    values.update(overrides)
    return ComponentReadinessEvidence(**values).with_computed_sha256()


def decide(expected, evidence, **overrides):
    component_ids = tuple(item.component_id for item in expected.components)
    values = {
        "decision_id": "readiness_decision_001",
        "now_ms": 10_000,
        "authenticated_component_ids": component_ids,
        "binary_verified_component_ids": component_ids,
    }
    values.update(overrides)
    return evaluate_readiness_contract(expected, evidence, **values)


class ReadinessTests(unittest.TestCase):
    def test_all_four_authenticated_exact_components_are_ready(self) -> None:
        expected = expectation()
        evidence = tuple(evidence_for(item) for item in expected.components)
        decision = decide(expected, evidence)
        self.assertEqual(decision.status, "READY")
        self.assertEqual(decision.http_status, 200)
        self.assertTrue(decision.has_valid_sha256())

    def test_schema_action_skill_component_or_artifact_mismatch_is_http_503(self) -> None:
        expected = expectation()
        cases = (
            ("schema_bundle_sha256", "readiness.schema_bundle.mismatch"),
            ("capability_manifest_sha256", "readiness.capability_manifest.mismatch"),
            ("skill_index_sha256", "readiness.skill_index.mismatch"),
            ("component_manifest_sha256", "readiness.component_manifest.mismatch"),
            (
                "contract_artifact_manifest_sha256",
                "readiness.contract_artifacts.mismatch",
            ),
        )
        for field, reason in cases:
            with self.subTest(field=field):
                evidence = [evidence_for(item) for item in expected.components]
                evidence[0] = evidence_for(expected.components[0], **{field: HASH_F})
                decision = decide(expected, evidence)
                self.assertEqual(decision.status, "NOT_READY")
                self.assertEqual(decision.http_status, 503)
                self.assertIn(reason, {item.reason_code for item in decision.failures})

    def test_health_claim_cannot_hide_missing_auth_tamper_stale_or_binary_mismatch(self) -> None:
        expected = expectation()
        evidence = [evidence_for(item) for item in expected.components]
        first = expected.components[0]
        evidence[0] = evidence_for(
            first,
            executable_sha256=HASH_F,
            observed_at_ms=1_000,
        )
        evidence[0] = evidence[0].model_copy(update={"evidence_sha256": HASH_A})
        authenticated = tuple(item.component_id for item in expected.components[1:])
        decision = decide(
            expected,
            evidence,
            authenticated_component_ids=authenticated,
        )
        reasons = {item.reason_code for item in decision.failures}
        self.assertEqual(decision.http_status, 503)
        self.assertIn("readiness.transport.unauthenticated", reasons)
        self.assertIn("readiness.evidence.digest_invalid", reasons)
        self.assertIn("readiness.evidence.stale", reasons)
        self.assertIn("readiness.component.binary_mismatch", reasons)

        decision = decide(expected, evidence[1:])
        self.assertIn(
            "readiness.component.missing",
            {item.reason_code for item in decision.failures},
        )

    def test_invalid_production_manifest_or_component_schema_is_rejected(self) -> None:
        manifest = component_manifest(production_claim=False)
        with self.assertRaises(ValueError):
            readiness_expectation_from_manifest(
                manifest,
                expectation_id="readiness_expectation_001",
                gateway_epoch=7,
                contract_artifact_manifest_sha256=HASH_E,
            )

        manifest = component_manifest()
        components = list(manifest.components)
        components[0] = components[0].model_copy(update={"schema_bundle_hash": HASH_F})
        changed = ComponentManifest(
            **{
                **manifest.model_dump(),
                "components": tuple(components),
                "manifest_sha256": HASH_F,
            }
        ).with_computed_manifest_sha256()
        with self.assertRaises(ValidationError):
            ReadinessExpectation(
                **{
                    **expectation().model_dump(),
                    "components": tuple(
                        item.model_copy(update={"schema_bundle_sha256": HASH_F})
                        if item.component_id == "tiangong-backend"
                        else item
                        for item in expectation().components
                    ),
                }
            )
        with self.assertRaises(ValueError):
            readiness_expectation_from_manifest(
                changed,
                expectation_id="readiness_expectation_002",
                gateway_epoch=7,
                contract_artifact_manifest_sha256=HASH_E,
            )

        manifest = component_manifest()
        components = list(manifest.components)
        components[0] = components[0].model_copy(update={"role": "communication"})
        wrong_role = ComponentManifest(
            **{
                **manifest.model_dump(),
                "components": tuple(components),
                "manifest_sha256": HASH_F,
            }
        ).with_computed_manifest_sha256()
        with self.assertRaises(ValueError):
            readiness_expectation_from_manifest(
                wrong_role,
                expectation_id="readiness_expectation_003",
                gateway_epoch=7,
                contract_artifact_manifest_sha256=HASH_E,
            )


if __name__ == "__main__":
    unittest.main()
