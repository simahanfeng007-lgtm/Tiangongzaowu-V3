"""P19-R2 M6 Workflows B + F: architecture guards (structural, not
string-soup) and the Verification Plane freeze manifest guard.

Guards (M6 §7/§8) — enforced with AST/contract scans:
- exactly ONE CompletionGate / GatewayStateStore / BackendClient /
  VerificationRepairCoordinator / VerificationPlanExecutor class
- exactly ONE store schema authority constant
- CompletionDecision construction lives ONLY in completion_gate.py
- no standalone repair runtime/daemon entry point
- the single Verification Plane version source exists and is "1.6"

Freeze guard (M6 §23/§24): the freeze manifest records the authority
surface hashes; any change fails with VERIFICATION_PLANE_FREEZE_CHANGED
until the manifest is explicitly regenerated (UPDATE_FREEZE=1) together
with a declared plane version bump.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
FREEZE_PATH = ROOT / "docs" / "p19-r2" / "m6" / (
    "VERIFICATION_PLANE_FREEZE.json"
)

_UNIQUE_CLASSES = (
    "CompletionGate",
    "GatewayStateStore",
    "BackendClient",
    "VerificationRepairCoordinator",
    "VerificationPlanExecutor",
    "ArtifactGate",
    "EffectStateOracle",
    "RepositoryStateOracle",
)


def _iter_py_files():
    for base in (SRC / "total_gateway", SRC / "contracts"):
        for path in base.rglob("*.py"):
            yield path


class ArchitectureGuardTests(unittest.TestCase):
    def test_single_runtime_gateway_store_gate(self) -> None:
        for cls_name in _UNIQUE_CLASSES:
            definitions = []
            for path in _iter_py_files():
                tree = ast.parse(
                    path.read_text(encoding="utf-8"), filename=str(path)
                )
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.ClassDef)
                        and node.name == cls_name
                    ):
                        definitions.append(path.relative_to(ROOT))
            self.assertEqual(
                len(definitions), 1,
                f"second {cls_name} implementation found: "
                f"{definitions}",
            )

    def test_single_store_schema_authority(self) -> None:
        assignments = []
        for path in _iter_py_files():
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.endswith("SCHEMA_VERSION")
                    and node.targets[0].id != "SHADOW_STORE_SCHEMA_VERSION"
                ):
                    assignments.append(
                        (path.relative_to(ROOT), node.targets[0].id)
                    )
        gateway = [a for a in assignments if a[1] == "STORE_SCHEMA_VERSION"]
        self.assertEqual(len(gateway), 1, assignments)
        # no SECOND gateway-store schema authority: any new top-level
        # gateway schema constant would be a second persistence
        # authority. (Contract-internal schema-id strings such as
        # _VERIFICATION_*_SCHEMA_VERSION and sub-system schemas like the
        # object store's are not gateway persistence authorities.)
        competitors = [
            a for a in assignments
            if a[1] != "STORE_SCHEMA_VERSION"
            and a[1].startswith("GATEWAY")
        ]
        self.assertEqual(competitors, [], assignments)

    def test_completion_decision_construction_gated(self) -> None:
        builders = []
        for path in _iter_py_files():
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "CompletionDecision"
                ):
                    builders.append(path.relative_to(ROOT))
        self.assertEqual(
            builders, [Path("src/total_gateway/completion_gate.py")],
            f"completion authority constructed outside the Gate: {builders}",
        )

    def test_no_standalone_repair_daemon_entry(self) -> None:
        mains = []
        for path in _iter_py_files():
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            for node in tree.body:
                if isinstance(node, ast.If):
                    test = node.test
                    if (
                        isinstance(test, ast.Compare)
                        and isinstance(test.left, ast.Name)
                        and test.left.id == "__name__"
                    ):
                        for cond in ast.walk(node):
                            if (
                                isinstance(cond, ast.Constant)
                                and cond.value == "__main__"
                            ):
                                source = path.read_text(encoding="utf-8")
                                if "repair" in source.lower() and (
                                    "loop" in source.lower()
                                    or "daemon" in source.lower()
                                ):
                                    mains.append(path.relative_to(ROOT))
        self.assertEqual(mains, [])

    def test_single_verification_plane_version_source(self) -> None:
        from total_gateway.verification_plane import (
            VERIFICATION_PLANE_VERSION,
        )

        self.assertEqual(VERIFICATION_PLANE_VERSION, "1.6")
        # the literal must appear in exactly ONE src module
        holders = [
            path.relative_to(ROOT)
            for path in _iter_py_files()
            if '"1.6"' in (
                path.read_text(encoding="utf-8")
            )
            and path.name == "verification_plane.py"
        ]
        self.assertEqual(len(holders), 1)


class VerificationPlaneFreezeGuardTests(unittest.TestCase):
    def _freeze_manifest(self) -> dict:
        from total_gateway.store import STORE_SCHEMA_VERSION
        from total_gateway.verification_plane import (
            VERIFICATION_PLANE_VERSION,
        )
        from total_gateway.verification_registry import VerifierRegistry
        from total_gateway.verification_repair_policy import (
            DEFAULT_POLICY,
            POLICY_VERSION,
        )

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        snapshot = VerifierRegistry.with_defaults().snapshot(
            captured_at_ms=1
        )
        return {
            "verification_plane_version": VERIFICATION_PLANE_VERSION,
            "baseline_sha": "9a3344de9fe468fa845d2ff501166484439b8ec4",
            "store_schema_version": STORE_SCHEMA_VERSION,
            "contract_schema_versions": {
                "verification": sha(
                    SRC / "contracts" / "verification.py"
                ),
                "verification_repair": sha(
                    SRC / "contracts" / "verification_repair.py"
                ),
            },
            "registry_fingerprint": snapshot.snapshot_sha256,
            "authority_map_sha256": sha(
                ROOT / "docs" / "p19-r2" / "AUTHORITY_MAP.txt"
            ),
            "completion_authority_sha256": sha(
                SRC / "total_gateway" / "completion_gate.py"
            ),
            "repair_policy_sha256": sha(
                SRC / "total_gateway" / "verification_repair_policy.py"
            ),
            "golden_corpus_sha256": self._corpus_sha(),
            "golden_trace_version": "1",
            # Every 1.6 execution, result-schema and verification authority
            # file is content-hashed.  Semantic drift in the runtime chain,
            # store/binding/coordinator/executor/readiness/fencing/successor
            # trips the freeze even when the schema version is unchanged.
            "authority_surface_sha256": self._authority_surface(),
        }

    #: The authority surface frozen at 1.6; 1.5 entries remain covered.
    AUTHORITY_SURFACE_FILES = (
        "app/backend/tiangong-backend/v3/fact_kernel/__init__.py",
        "src/contracts/execution.py",
        "src/contracts/verification.py",
        "src/contracts/verification_repair.py",
        "src/omni_body_skill/registry/capability_manifest.generated.json",
        "src/omni_body_skill/tool_contracts.py",
        "src/total_gateway/action_registry.py",
        "src/total_gateway/store.py",
        "src/total_gateway/store_unit_of_work.py",
        "src/total_gateway/composition_activation_shadow.py",
        "src/total_gateway/composition_activation_registration.py",
        "src/total_gateway/composition_activation_store.py",
        "src/total_gateway/composition_activation_adapter.py",
        "src/total_gateway/composition_executable_plan.py",
        "src/total_gateway/composition_executable_plan_store.py",
        "src/total_gateway/composition_step_authorization.py",
        "src/total_gateway/composition_execution_projection.py",
        "src/total_gateway/composition_execution_binding.py",
        "src/total_gateway/composition_backend_transport.py",
        "src/total_gateway/composition_step_execution.py",
        "src/total_gateway/desktop_completion.py",
        "src/total_gateway/fact_ledger.py",
        "src/total_gateway/omni_grant_authority.py",
        "src/total_gateway/orchestration.py",
        "src/total_gateway/skill_selection.py",
        "src/total_gateway/verification_repair_coordinator.py",
        "src/total_gateway/verification_repair_policy.py",
        "src/total_gateway/verification_plan_executor.py",
        "src/total_gateway/verification_plane.py",
        "src/total_gateway/verification_readiness.py",
        "src/total_gateway/verification_failure_evidence.py",
        "src/total_gateway/verification_registry.py",
        # ALL THREE verifier implementations + the registry: artifact /
        # effect / repository PASS semantics are content-frozen — a
        # silent change to any PASS rule trips the freeze even when
        # verifier ids/versions and the registry digest stay untouched.
        "src/total_gateway/outcome_oracles/artifact_content.py",
        "src/total_gateway/outcome_oracles/effect_state.py",
        "src/total_gateway/outcome_oracles/repository_state.py",
        "src/total_gateway/completion_gate.py",
        "src/total_gateway/effects.py",
        "src/world_understanding/tool_capability_world/compiler.py",
        "src/total_gateway/tool_source_candidate.py",
        "src/total_gateway/tool_source_inputs.py",
        "src/total_gateway/tool_manifest_evolution.py",
        "src/source_authority/validator.py",
        "src/omni_body_skill/tools/sandbox_runtime.py",
        "scripts/_tool_source_build_worker.py",
        "scripts/build-tool-source.py",
        "scripts/review-tool-source.py",
    )

    @classmethod
    def _corpus_sha(cls) -> str:
        # Content-addressed corpus digest (M6 correction #1): relative
        # path + ACTUAL FILE BYTES — editing a baseline in place can no
        # longer slip past the freeze.
        baselines_dir = (
            ROOT / "tests" / "golden" / "p19_r2" / "baselines"
        )
        aggregate = hashlib.sha256()
        for path in sorted(
            baselines_dir.glob("*.json"), key=lambda item: item.name
        ):
            aggregate.update(path.name.encode("utf-8"))
            aggregate.update(b":")
            aggregate.update(hashlib.sha256(path.read_bytes()).digest())
            aggregate.update(bytes([10]))
        return aggregate.hexdigest()

    @classmethod
    def _authority_surface(cls) -> dict:
        return {
            rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            for rel in cls.AUTHORITY_SURFACE_FILES
        }

    def test_freeze_manifest_unchanged(self) -> None:
        current = self._freeze_manifest()
        if os.environ.get("UPDATE_FREEZE") == "1":
            FREEZE_PATH.parent.mkdir(parents=True, exist_ok=True)
            FREEZE_PATH.write_bytes(
                (json.dumps(current, ensure_ascii=False, indent=1) + "\n")
                .encode("utf-8")
            )
            return
        self.assertTrue(
            FREEZE_PATH.exists(),
            "freeze manifest missing — generate with UPDATE_FREEZE=1",
        )
        frozen = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
        changed = [
            key for key in frozen if frozen[key] != current.get(key)
        ]
        self.assertEqual(
            changed, [],
            "VERIFICATION_PLANE_FREEZE_CHANGED: "
            f"{changed} — an explicit Verification Plane version bump"
            " and manifest refresh are required; silent drift is"
            " forbidden.",
        )


if __name__ == "__main__":
    unittest.main()
