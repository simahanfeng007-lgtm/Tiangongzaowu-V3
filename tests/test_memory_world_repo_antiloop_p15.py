"""P15 M7: memory cannot echo WU/GIT output into a new evidence group."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from contracts.cognition_evidence import (
    CognitionEvidence,
    CognitionSourceRef,
    derive_cognition_evidence_id,
)
from contracts.canonical import canonical_sha256
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event
from world_understanding.cognition.memory_candidate import (
    MemoryWorldCandidateBridge,
)
from world_understanding.cognition.store import WorldCognitionStore


LIFE = "life_p15_world_antiloop"
PRINCIPAL = "principal_test"
PRIVACY = "private"
ROOT = "55" * 32


def wu_echo_evidence(*, root: str) -> CognitionEvidence:
    source = CognitionSourceRef(
        source_kind="code_perception",
        object_id="git_frame_1",
        object_revision=1,
        sha256=canonical_sha256({"git": "frame"}),
    )
    evidence_id = derive_cognition_evidence_id(
        life_id=LIFE,
        domain="external",
        world_scope_hash="11" * 32,
        principal_scope_hash="22" * 32,
        privacy_scope="private",
        source_ref=source,
        evidence_class="observed",
        source_credibility_milli=1000,
        authority_ceiling_milli=1000,
        provenance_integrity_milli=1000,
        observation_mode="positive",
        observation="repository claim",
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=canonical_sha256({"group": "echo"}),
        lineage_root_hashes=(root,),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id="git_frame_1",
        content_sha256=canonical_sha256({"git": "frame"}),
        extractor_kind="direct_tool",
        observed_at_ms=3_500,
        valid_from_ms=3_500,
        valid_until_ms=None,
        volatility_class="structural",
    )
    return CognitionEvidence(
        schema_version="tiangong.cognition.contracts.v1",
        evidence_id=evidence_id,
        life_id=LIFE,
        domain="external",
        world_scope_hash="11" * 32,
        principal_scope_hash="22" * 32,
        privacy_scope="private",
        source_ref=source,
        evidence_class="observed",
        source_credibility_milli=1000,
        authority_ceiling_milli=1000,
        provenance_integrity_milli=1000,
        observation_mode="positive",
        observation="repository claim",
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=canonical_sha256({"group": "echo"}),
        lineage_root_hashes=(root,),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id="git_frame_1",
        content_sha256=canonical_sha256({"git": "frame"}),
        extractor_kind="direct_tool",
        observed_at_ms=3_500,
        valid_from_ms=3_500,
        valid_until_ms=None,
        volatility_class="structural",
        evidence_sha256="0" * 64,
    ).with_computed_evidence_sha256()


class MemoryWorldRepoAntiloopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cognition = WorldCognitionStore(self.root / "wu")
        self.bridge = MemoryWorldCandidateBridge(self.cognition)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_git_echo_candidate_is_rejected(self) -> None:
        self.bridge.ledger.ingest(wu_echo_evidence(root=ROOT))
        from contracts.world_understanding.memory_candidate import (
            MemoryWorldCandidate,
        )

        candidate = MemoryWorldCandidate(
            candidate_id="wmc_" + "1" * 64,
            life_id=LIFE,
            world_scope_hash="11" * 32,
            principal_scope_hash="22" * 32,
            source_memory_id="mem_" + "3" * 64,
            source_memory_revision=1,
            source_assertion_sha256="44" * 32,
            source_derivation_id="mdr_" + "5" * 64,
            source_layer="L3_EXPERIENCE",
            claim_key="claim:echo",
            semantic_payload="repository says X",
            evidence_refs=(),
            lineage_root_hashes=(ROOT,),
            epistemic_status="user_asserted",
            confidence_milli=750,
            volatility_class="medium",
            valid_from_ms=1_000,
            valid_until_ms=None,
            privacy_scope="private",
            candidate_sha256="0" * 64,
        ).with_computed_candidate_sha256()
        outcome = self.bridge.ingest(candidate, now_ms=4_000)
        self.assertEqual(outcome["outcome"], "echo_only")

    def test_candidate_with_fresh_memory_root_is_accepted(self) -> None:
        from contracts.world_understanding.memory_candidate import (
            MemoryWorldCandidate,
        )

        candidate = MemoryWorldCandidate(
            candidate_id="wmc_" + "2" * 64,
            life_id=LIFE,
            world_scope_hash="11" * 32,
            principal_scope_hash="22" * 32,
            source_memory_id="mem_" + "3" * 64,
            source_memory_revision=1,
            source_assertion_sha256="44" * 32,
            source_derivation_id="mdr_" + "6" * 64,
            source_layer="L3_EXPERIENCE",
            claim_key="claim:fresh",
            semantic_payload="observed via tool",
            evidence_refs=(),
            lineage_root_hashes=("77" * 32,),
            epistemic_status="observed",
            confidence_milli=1000,
            volatility_class="medium",
            valid_from_ms=1_000,
            valid_until_ms=None,
            privacy_scope="private",
            candidate_sha256="0" * 64,
        ).with_computed_candidate_sha256()
        outcome = self.bridge.ingest(candidate, now_ms=4_000)
        self.assertEqual(outcome["outcome"], "accepted")

    def test_partial_echo_root_still_keeps_independence(self) -> None:
        self.bridge.ledger.ingest(wu_echo_evidence(root=ROOT))
        from contracts.world_understanding.memory_candidate import (
            MemoryWorldCandidate,
        )

        candidate = MemoryWorldCandidate(
            candidate_id="wmc_" + "3" * 64,
            life_id=LIFE,
            world_scope_hash="11" * 32,
            principal_scope_hash="22" * 32,
            source_memory_id="mem_" + "3" * 64,
            source_memory_revision=1,
            source_assertion_sha256="44" * 32,
            source_derivation_id="mdr_" + "7" * 64,
            source_layer="L3_EXPERIENCE",
            claim_key="claim:partial",
            semantic_payload="partly echoed",
            evidence_refs=(),
            lineage_root_hashes=(ROOT, "88" * 32),
            epistemic_status="user_asserted",
            confidence_milli=750,
            volatility_class="medium",
            valid_from_ms=1_000,
            valid_until_ms=None,
            privacy_scope="private",
            candidate_sha256="0" * 64,
        ).with_computed_candidate_sha256()
        # One fresh root exists, so the candidate keeps its independence.
        self.assertTrue(
            self.bridge.has_independent_reality_root(candidate)
        )

    def test_memory_compiler_direct_known_authority_stays_zero(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "world_understanding"
            / "source_compilers"
            / "p3.py"
        ).read_text(encoding="utf-8")
        matches = re.findall(
            r'CompilerSpec\("MEMORY"[^)]*?,\s*(\d+)\s*,\s*(\d+)\s*\)', text
        )
        self.assertTrue(matches)
        self.assertTrue(
            all(a == "0" and b == "0" for a, b in matches)
        )

    def test_git_code_original_path_is_not_touched(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "world_understanding"
            / "source_compilers"
            / "p3.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'CompilerSpec("GIT_CODE"', text
        )
        self.assertNotIn(
            'CompilerSpec("MEMORY",' ' "wu.compiler.git-code"', text
        )

    def test_model_synthesis_echo_is_not_independent(self) -> None:
        source = CognitionSourceRef(
            source_kind="model_synthesis",
            object_id="synthesis_1",
            object_revision=1,
            sha256=canonical_sha256({"model": "synth"}),
        )
        evidence_id = derive_cognition_evidence_id(
            life_id=LIFE,
            domain="external",
            world_scope_hash="11" * 32,
            principal_scope_hash="22" * 32,
            privacy_scope="private",
            source_ref=source,
            evidence_class="model_inference",
            source_credibility_milli=0,
            authority_ceiling_milli=0,
            provenance_integrity_milli=0,
            observation_mode="positive",
            observation="model said X",
            coverage_milli=0,
            search_scope_hash=None,
            independence_group_hash=canonical_sha256({"g": "synth"}),
            lineage_root_hashes=("aa" * 32,),
            derived_from_evidence_ids=(),
            ancestor_cognition_ids=(),
            content_object_id="synthesis_1",
            content_sha256=canonical_sha256({"model": "synth"}),
            extractor_kind="llm_synthesis",
            observed_at_ms=3_500,
            valid_from_ms=3_500,
            valid_until_ms=None,
            volatility_class="medium",
        )
        echo = CognitionEvidence(
            schema_version="tiangong.cognition.contracts.v1",
            evidence_id=evidence_id,
            life_id=LIFE,
            domain="external",
            world_scope_hash="11" * 32,
            principal_scope_hash="22" * 32,
            privacy_scope="private",
            source_ref=source,
            evidence_class="model_inference",
            source_credibility_milli=0,
            authority_ceiling_milli=0,
            provenance_integrity_milli=0,
            observation_mode="positive",
            observation="model said X",
            coverage_milli=0,
            search_scope_hash=None,
            independence_group_hash=canonical_sha256({"g": "synth"}),
            lineage_root_hashes=("aa" * 32,),
            derived_from_evidence_ids=(),
            ancestor_cognition_ids=(),
            content_object_id="synthesis_1",
            content_sha256=canonical_sha256({"model": "synth"}),
            extractor_kind="llm_synthesis",
            observed_at_ms=3_500,
            valid_from_ms=3_500,
            valid_until_ms=None,
            volatility_class="medium",
            evidence_sha256="0" * 64,
        ).with_computed_evidence_sha256()
        self.bridge.ledger.ingest(echo)
        from contracts.world_understanding.memory_candidate import (
            MemoryWorldCandidate,
        )

        candidate = MemoryWorldCandidate(
            candidate_id="wmc_" + "4" * 64,
            life_id=LIFE,
            world_scope_hash="11" * 32,
            principal_scope_hash="22" * 32,
            source_memory_id="mem_" + "3" * 64,
            source_memory_revision=1,
            source_assertion_sha256="44" * 32,
            source_derivation_id="mdr_" + "8" * 64,
            source_layer="L3_EXPERIENCE",
            claim_key="claim:synth-echo",
            semantic_payload="model said X",
            evidence_refs=(),
            lineage_root_hashes=("aa" * 32,),
            epistemic_status="user_asserted",
            confidence_milli=750,
            volatility_class="medium",
            valid_from_ms=1_000,
            valid_until_ms=None,
            privacy_scope="private",
            candidate_sha256="0" * 64,
        ).with_computed_candidate_sha256()
        outcome = self.bridge.ingest(candidate, now_ms=4_000)
        self.assertEqual(outcome["outcome"], "echo_only")

    def test_git_code_spec_keeps_full_authority(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "world_understanding"
            / "source_compilers"
            / "p3.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'CompilerSpec("GIT_CODE","wu.compiler.git-code",'
            '"v0.1","GIT_OBSERVED","git.observation","GIT_CODE",1000,1000)',
            text,
        )


if __name__ == "__main__":
    unittest.main()
