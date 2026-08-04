from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from total_gateway.object_gc import analyze_object_gc_dry_run, build_object_gc_dry_run
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore, ObjectOwnerRecord


SCOPE = "a" * 64
DAY_MS = 24 * 60 * 60 * 1000


class ObjectGcDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.objects = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
        self.gateway = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=1_000)

    def tearDown(self) -> None:
        self.gateway.close()
        self.objects.close()
        self.temporary.cleanup()

    def put(self, data: bytes, *, tenant: str, created_at_ms: int = 1_100):
        return self.objects.put_bytes(
            data,
            kind="artifact",
            tenant_id=tenant,
            link_account_id="link",
            conversation_scope_hash=SCOPE,
            created_at_ms=created_at_ms,
        ).reference

    def test_dry_run_never_marks_owner_revision_hold_or_young_reference(self) -> None:
        old_unowned = self.put(b"old-unowned", tenant="a")
        old_owned = self.put(b"shared", tenant="b")
        shared_unowned = self.put(b"shared", tenant="c")
        old_revision = self.put(b"revision", tenant="d")
        legal_hold = self.put(b"legal-hold", tenant="e")
        young = self.put(b"young", tenant="f", created_at_ms=25 * DAY_MS)
        revision, _ = self.objects.register_revision(
            "logical-artifact",
            1,
            old_revision.object_id,
            manifest_sha256="b" * 64,
            created_at_ms=2_000,
        )
        owner = ObjectOwnerRecord(
            object_id=old_owned.object_id,
            object_sha256=old_owned.sha256,
            owner_kind="ARTIFACT",
            owner_id="artifact-owner",
            request_id="request",
            run_id="run",
            generation=1,
            created_at_ms=2_000,
            ownership_sha256="c" * 64,
            created_by_this_call=False,
            duplicate=False,
        )
        references_before = self.objects.list_references()
        report = analyze_object_gc_dry_run(
            references_before,
            (revision,),
            (owner,),
            now_ms=30 * DAY_MS,
            minimum_unowned_age_ms=7 * DAY_MS,
            legal_hold_object_ids=(legal_hold.object_id,),
        )
        candidate_ids = {candidate.object_id for candidate in report.candidates}
        self.assertEqual(candidate_ids, {old_unowned.object_id, shared_unowned.object_id})
        self.assertNotIn(old_owned.object_id, candidate_ids)
        self.assertNotIn(old_revision.object_id, candidate_ids)
        self.assertNotIn(legal_hold.object_id, candidate_ids)
        self.assertNotIn(young.object_id, candidate_ids)
        by_id = {candidate.object_id: candidate for candidate in report.candidates}
        self.assertTrue(by_id[old_unowned.object_id].content_reclaimable_if_applied)
        self.assertFalse(by_id[shared_unowned.object_id].content_reclaimable_if_applied)
        self.assertTrue(report.dry_run)
        self.assertTrue(report.has_valid_report_sha256())
        self.assertEqual(self.objects.list_references(), references_before)
        for reference in references_before:
            self.assertEqual(
                self.objects.read_bytes(reference.object_id),
                {
                    old_unowned.object_id: b"old-unowned",
                    old_owned.object_id: b"shared",
                    shared_unowned.object_id: b"shared",
                    old_revision.object_id: b"revision",
                    legal_hold.object_id: b"legal-hold",
                    young.object_id: b"young",
                }[reference.object_id],
            )

    def test_integrated_report_is_read_only_and_hash_stable(self) -> None:
        reference = self.put(b"unowned", tenant="a")
        first = build_object_gc_dry_run(
            self.objects,
            self.gateway,
            now_ms=20 * DAY_MS,
            minimum_unowned_age_ms=7 * DAY_MS,
        )
        second = build_object_gc_dry_run(
            self.objects,
            self.gateway,
            now_ms=20 * DAY_MS,
            minimum_unowned_age_ms=7 * DAY_MS,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.candidate_count, 1)
        self.assertEqual(first.candidates[0].object_id, reference.object_id)
        self.assertIsNotNone(self.objects.get_reference(reference.object_id))


if __name__ == "__main__":
    unittest.main()
