from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from contracts import LifeSnapshot, canonical_sha256
from life_service.context_api import LifeContextCompileAuthorizeApi, LifeProjectionInputs
from life_service.store import LifeShadowStore
from total_gateway.frozen_backend_compat import (
    FrozenBackendCompatibilityError,
    FrozenBackendCompatibilityTransport,
    _backend_terminal_projection,
    _first_text,
    _legacy_json_bytes,
)


class _SyntheticLifeTransport:
    def request(self, *_args, **_kwargs):
        return (
            200,
            {
                "ok": True,
                "recovered": True,
                "state": "completed",
                "presentation_score": 0.875,
                "nested": {"energy": 0.5},
            },
            "a" * 64,
        )


class _SyntheticLongRunTransport:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        request_id = "req_" + "4" * 64
        if self.calls == 1:
            return (
                200,
                {"run": {"request_id": request_id, "status": "RUNNING_MODEL", "event_seq": 12}},
                "b" * 64,
            )
        return (
            200,
            {
                "run": {
                    "request_id": request_id,
                    "status": "COMPLETED",
                    "event_seq": 19,
                    "final_response": "长任务已从断点完成",
                }
            },
            "c" * 64,
        )


class _SyntheticCompactionLifeTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LifeShadowStore.open(
            Path(self.temporary.name) / "compat.shadow.sqlite3",
            create=True,
            now_ms=100,
        )
        self.api = LifeContextCompileAuthorizeApi(self.store)

    def close(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def request(self, _method, _path, payload, **_kwargs):
        self.calls += 1
        result = self.api.compile_and_authorize(
            payload,
            LifeProjectionInputs(
                life_id="life_compaction",
                writer_epoch=2,
                identity_revision=2,
                soul={"life_id": "life_compaction", "revision": 1, "name": "起源", "prompt": "权威 Soul 人格底稿"},
                capabilities={},
            ),
        )
        return 200, result, canonical_sha256(result)


class FrozenBackendCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _attachment_materialization_case(
        workspace_root: Path,
        filename: str,
        *,
        data: bytes = b"attachment-content",
    ) -> tuple[FrozenBackendCompatibilityTransport, SimpleNamespace, dict[str, object]]:
        object_id = "oref_" + "1" * 64
        sha256 = "2" * 64
        conversation_scope_hash = "3" * 64
        grant = SimpleNamespace(
            object_id=object_id,
            revision=1,
            sha256=sha256,
            size_bytes=len(data),
            mime="text/markdown",
            tenant_id="tenant_test",
            link_account_id="desktop_test",
            conversation_scope_hash=conversation_scope_hash,
        )
        reference = SimpleNamespace(
            sha256=sha256,
            size_bytes=len(data),
            tenant_id=grant.tenant_id,
            link_account_id=grant.link_account_id,
            conversation_scope_hash=conversation_scope_hash,
        )
        transport = object.__new__(FrozenBackendCompatibilityTransport)
        transport._workspace_root = workspace_root  # noqa: SLF001
        transport._objects = SimpleNamespace(  # noqa: SLF001
            get_reference=lambda _object_id: reference,
            read_bytes=lambda _object_id: data,
        )
        ticket = SimpleNamespace(
            payload=SimpleNamespace(
                channel="desktop",
                request_id="req_" + "4" * 64,
                input_objects=(grant,),
            )
        )
        arguments: dict[str, object] = {
            "attachments": [
                {
                    "object_id": object_id,
                    "revision": grant.revision,
                    "filename": filename,
                }
            ]
        }
        return transport, ticket, arguments

    def test_materialize_inputs_accepts_nfc_chinese_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transport, ticket, arguments = self._attachment_materialization_case(
                Path(temporary),
                "母亲的灯.md",
            )
            materialized = transport._materialize_inputs(ticket, arguments)  # noqa: SLF001

            self.assertEqual(materialized[0]["filename"], "母亲的灯.md")
            materialized_path = Path(materialized[0]["path"])
            self.assertEqual(
                materialized_path.name,
                "2" * 64 + ".md",
            )
            self.assertEqual(materialized_path.read_bytes(), b"attachment-content")

    def test_materialize_customer_hash_named_76kb_markdown_stays_below_max_path(self) -> None:
        body = b"# Synthetic attachment regression\n" + (
            b"A" * (78_234 - len(b"# Synthetic attachment regression\n"))
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / ("long-workspace-" + "x" * 48)
            workspace.mkdir()
            transport, ticket, arguments = self._attachment_materialization_case(
                workspace,
                "f10a77e1fd9f73c60b4b2b6a4b5db904.md",
                data=body,
            )

            materialized = transport._materialize_inputs(ticket, arguments)  # noqa: SLF001

            self.assertEqual(
                materialized[0]["filename"],
                "f10a77e1fd9f73c60b4b2b6a4b5db904.md",
            )
            materialized_path = Path(materialized[0]["path"])
            self.assertEqual(materialized_path.name, "2" * 64 + ".md")
            self.assertLessEqual(len(str(materialized_path)) + len(".tmp"), 240)
            self.assertEqual(materialized_path.read_bytes(), body)

    def test_materialize_inputs_rejects_unsafe_filename_with_compat_code(self) -> None:
        for filename in ("../escape.md", "control\x00name.md", "CON.txt"):
            with self.subTest(filename=repr(filename)):
                with tempfile.TemporaryDirectory() as temporary:
                    transport, ticket, arguments = self._attachment_materialization_case(
                        Path(temporary),
                        filename,
                    )
                    with self.assertRaises(FrozenBackendCompatibilityError) as raised:
                        transport._materialize_inputs(ticket, arguments)  # noqa: SLF001

                self.assertEqual(
                    raised.exception.code,
                    "compat.attachment.filename_unsafe",
                )

    def test_legacy_http_encoder_allows_finite_presentation_floats_only(self) -> None:
        self.assertEqual(
            _legacy_json_bytes({"score": 0.875, "ok": True}),
            b'{"ok":true,"score":0.875}',
        )
        with self.assertRaisesRegex(
            FrozenBackendCompatibilityError,
            "compat.http.request_json_invalid",
        ):
            _legacy_json_bytes({"score": float("nan")})

    def test_recovery_projects_float_free_machine_evidence(self) -> None:
        transport = object.__new__(FrozenBackendCompatibilityTransport)
        transport._life = _SyntheticLifeTransport()  # noqa: SLF001
        evidence = transport._recover("req_test", "cycle_test")  # noqa: SLF001
        self.assertEqual(
            evidence,
            {
                "http_status": 200,
                "ok": True,
                "recovered": True,
                "state": "completed",
                "response_sha256": "a" * 64,
            },
        )
        self.assertRegex(canonical_sha256(evidence), r"^[0-9a-f]{64}$")

    def test_backend_safe_pause_is_never_classified_as_success(self) -> None:
        projected = _backend_terminal_projection(
            {
                "run": {
                    "request_id": "req_" + "1" * 64,
                    "status": "BLOCKED",
                    "stage": "EFFECT_UNKNOWN",
                    "event_seq": 42,
                    "final_response": "safe pause",
                }
            },
            "req_" + "1" * 64,
        )
        self.assertEqual(projected["classification"], "AMBIGUOUS")
        self.assertEqual(projected["reason_code"], "compat.backend.effect_unknown")

    def test_backend_running_return_is_outcome_unknown(self) -> None:
        projected = _backend_terminal_projection(
            {"run": {"request_id": "req_" + "2" * 64, "status": "RUNNING_MODEL"}},
            "req_" + "2" * 64,
        )
        self.assertEqual(projected["classification"], "AMBIGUOUS")
        self.assertEqual(projected["reason_code"], "compat.backend.nonterminal_return")

    def test_backend_completed_status_is_success(self) -> None:
        projected = _backend_terminal_projection(
            {"run": {"gateway_request_id": "req_" + "3" * 64, "status": "COMPLETED"}},
            "req_" + "3" * 64,
        )
        self.assertEqual(projected["classification"], "SUCCEEDED")

    def test_backend_finished_with_explicit_failed_outcome_is_not_success(self) -> None:
        projected = _backend_terminal_projection(
            {
                "run": {
                    "gateway_request_id": "req_" + "6" * 64,
                    "phase": "finished",
                    "ok": False,
                }
            },
            "req_" + "6" * 64,
        )
        self.assertEqual(projected["classification"], "FAILED_FINAL")
        self.assertEqual(projected["reason_code"], "compat.backend.reported_failure")
        self.assertIs(projected["backend_ok"], False)

    def test_backend_a5_wait_is_an_authorization_checkpoint_not_terminal_failure(self) -> None:
        projected = _backend_terminal_projection(
            {
                "run": {
                    "request_id": "req_" + "5" * 64,
                    "status": "WAITING_FOR_USER",
                    "stage": "A5_AUTHORITY_REQUIRED",
                }
            },
            "req_" + "5" * 64,
        )
        self.assertEqual(projected["classification"], "WAITING_FOR_USER")
        self.assertEqual(projected["reason_code"], "compat.backend.waiting_for_user")

    def test_status_payload_exposes_persisted_final_response(self) -> None:
        self.assertEqual(
            _first_text({"run": {"final_response": "长任务已从断点完成"}}),
            "长任务已从断点完成",
        )

    def test_timed_out_http_call_is_reconciled_until_durable_terminal_state(self) -> None:
        transport = object.__new__(FrozenBackendCompatibilityTransport)
        transport._backend = _SyntheticLongRunTransport()  # noqa: SLF001
        request_id = "req_" + "4" * 64
        status, payload, response_sha256, projected = transport._wait_backend_terminal(  # noqa: SLF001
            request_id,
            deadline_monotonic=__import__("time").monotonic() + 5.0,
        )
        self.assertEqual(status, 200)
        self.assertEqual(response_sha256, "c" * 64)
        self.assertEqual(projected["classification"], "SUCCEEDED")
        self.assertEqual(_first_text(payload), "长任务已从断点完成")

    def test_skill_candidate_and_model_request_channel_share_trusted_context(self) -> None:
        context = FrozenBackendCompatibilityTransport._skill_routing_context(  # noqa: SLF001
            {
                "skill_recommendation": {
                    "origin": "system_recommendation",
                    "decision": "defer",
                    "activation_state": "candidate",
                    "selected_skill_id": "skill.word",
                    "candidates": [
                        {
                            "skill_id": "skill.word",
                            "version": "v1",
                            "sha256": "a" * 64,
                            "compatible": True,
                            "missing_actions": [],
                        }
                    ],
                }
            }
        )
        self.assertEqual(
            context["system_matching"]["selected_candidate"]["skill_id"],
            "skill.word",
        )
        self.assertTrue(context["system_matching"]["candidate_is_not_activated"])
        self.assertEqual(context["system_matching"]["activation_state"], "candidate")
        self.assertEqual(
            context["model_request"]["operations"],
            ["skill.route", "skill.list", "skill.get", "skill.read"],
        )
        self.assertEqual(
            context["model_request"]["activation_operations"],
            ["skill.get", "skill.read"],
        )
        self.assertFalse(context["model_request"]["procedure_loaded"])

    def test_context_compile_and_authorize_is_one_bound_call(self) -> None:
        soul = {"life_id": "life_compaction", "revision": 1, "name": "起源", "prompt": "权威 Soul 人格底稿"}
        snapshot = LifeSnapshot(
            snapshot_id="life_snapshot_compaction",
            revision=3,
            sha256="a" * 64,
            created_at_ms=1_000,
            identity_ref="life_compaction",
            identity_revision=2,
            persona_name="起源",
            user_callsign="老板",
            compiled_context_object_id="context_compaction",
            compiled_context_sha256="b" * 64,
            soul_sha256=canonical_sha256(soul),
            memory_revision=0,
            affect_revision=0,
            capability_profile_hash="d" * 64,
        )
        callbacks: list[tuple[dict[str, object], int]] = []
        transport = object.__new__(FrozenBackendCompatibilityTransport)
        life_transport = _SyntheticCompactionLifeTransport()
        transport._life = life_transport  # noqa: SLF001
        transport._on_context_compaction = (  # noqa: SLF001
            lambda envelope, observed_at_ms: callbacks.append(
                (dict(envelope), observed_at_ms)
            )
        )
        ticket = SimpleNamespace(
            payload=SimpleNamespace(
                life_snapshot_hash=snapshot.sha256,
                life_snapshot_revision=snapshot.revision,
                request_id="req_" + "5" * 64,
                run_id="run_" + "6" * 64,
                generation=1,
                channel="desktop",
            )
        )
        try:
            prepared = transport._prepare_life(  # noqa: SLF001
                ticket,
                {
                    "life_snapshot": snapshot.model_dump(mode="python"),
                    "text": "继续超长任务",
                    "recent_messages": [],
                },
            )
        finally:
            life_transport.close()
        self.assertEqual(life_transport.calls, 1)
        self.assertTrue(prepared["context_hash"])
        self.assertEqual(prepared["binding_status"], "authorized")
        self.assertEqual(prepared["context_envelope"]["soul"]["prompt"], "权威 Soul 人格底稿")
        self.assertEqual(callbacks, [])

if __name__ == "__main__":
    unittest.main()
