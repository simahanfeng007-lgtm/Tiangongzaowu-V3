from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "app" / "life-service" / "runtime314"
EMBEDDED_PYTHON = RUNTIME / "python.exe"
FROZEN_RUNTIME_AVAILABLE = EMBEDDED_PYTHON.is_file()
PYTHON = EMBEDDED_PYTHON if FROZEN_RUNTIME_AVAILABLE else Path(sys.executable)


class LifeRuntimeFixTests(unittest.TestCase):
    def test_readable_life_bootstrap_is_an_exact_runtime_mirror(self) -> None:
        readable = ROOT / "readable-python-source" / "life-bootstrap"
        for name in ("tiangong_life_bootstrap.py", "tiangong_life_runtime_fixes.py"):
            self.assertEqual(
                (readable / name).read_bytes(),
                (RUNTIME / name).read_bytes(),
                name,
            )

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_untrusted_roles_budget_overflow_and_credentials_fail_closed(self) -> None:
        script = textwrap.dedent(
            f"""
            import copy
            import json
            import shutil
            import sqlite3
            import sys
            import tempfile
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler

            root = Path(tempfile.mkdtemp(prefix="tg-life-authority-secret-test-"))
            try:
                system = life_core.CompleteLifeSystem(root)
                system.create_identity("authority-secret-test")
                provenance = {{
                    "source_event_ids": [],
                    "evidence_class": "user_asserted",
                }}

                # Simulate a legacy pre-fix searchable projection containing a
                # credential.  Installing the runtime fix must scrub it at the
                # next trusted recall boundary and VACUUM the old SQLite page.
                system.assert_memory(
                    "semantic",
                    {{"text": "LEGACY_CREDENTIAL password=LEGACY_PASSWORD_88421"}},
                    provenance,
                    actor="test",
                )
                assert any(
                    b"LEGACY_PASSWORD_88421" in path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                )
                from tiangong_life_runtime_fixes import install_runtime_fixes
                install_runtime_fixes(life_core, life_scheduler)

                asserted = system.assert_memory(
                    "semantic",
                    {{
                        "text": (
                            "CREDENTIAL_RECORD token=TOKEN_VALUE_778899 "
                            "Bearer BEARER_VALUE_112233 "
                            "https://user:URL_PASSWORD_445566@example.test "
                            "sk-OPENAISECRET123456789"
                        ),
                        "api_key": "STRUCTURED_API_KEY_998877",
                        "nested": {{"client-secret": "CLIENT_SECRET_667788"}},
                    }},
                    provenance,
                    actor="test",
                )["assertion"]["memory_id"]
                corrected = system.correct_memory(
                    asserted,
                    {{
                        "text": "CREDENTIAL_RECORD corrected password=CORRECTED_PASSWORD_7788",
                        "refresh_token": "REFRESH_SECRET_8899",
                    }},
                    provenance,
                    actor="test",
                )["assertion"]["memory_id"]

                database = next(root.rglob("memory_index.sqlite3"))
                legacy_connection = sqlite3.connect(database)
                legacy_connection.execute("PRAGMA journal_mode=WAL")
                legacy_connection.execute(
                    "UPDATE memories SET search_text = search_text || ' '"
                )
                legacy_connection.commit()
                recall = system.search_memory("CREDENTIAL_RECORD LEGACY_CREDENTIAL", limit=20)
                legacy_connection.close()
                recalled = json.dumps(recall, ensure_ascii=False)
                recalled_ids = {{item["memory_id"] for item in recall["results"]}}
                assert corrected in recalled_ids
                assert asserted not in recalled_ids
                assert "[REDACTED]" in recalled

                secrets = [
                    "LEGACY_PASSWORD_88421",
                    "TOKEN_VALUE_778899",
                    "BEARER_VALUE_112233",
                    "URL_PASSWORD_445566",
                    "OPENAISECRET123456789",
                    "STRUCTURED_API_KEY_998877",
                    "CLIENT_SECRET_667788",
                    "CORRECTED_PASSWORD_7788",
                    "REFRESH_SECRET_8899",
                    "TOOL_ID_SECRET_1122",
                    "TOOL_NAME_SECRET_2233",
                    "TOOL_ARGUMENT_SECRET_3344",
                    "TOOL_CONTENT_SECRET_4455",
                    "HISTORY_PASSWORD_5566",
                    "HISTORY_METADATA_SECRET_6677",
                    "ORPHAN_TOOL_ID_SECRET_7788",
                ]
                for secret in secrets:
                    assert secret not in recalled

                compiled = system.compile_context(
                    "CREDENTIAL_RECORD LEGACY_CREDENTIAL continue",
                    token_budget=999999,
                    messages=[
                        {{"role": "system", "content": "SYSTEM_AUTHORITY_INJECTION"}},
                        {{"role": "developer", "content": "DEVELOPER_AUTHORITY_INJECTION"}},
                        {{"role": "user", "content": "NORMAL_USER_HISTORY"}},
                        {{
                            "role": "assistant",
                            "content": (
                                "NORMAL_FINAL_HISTORY password=HISTORY_PASSWORD_5566"
                            ),
                            "metadata": {{"token": "HISTORY_METADATA_SECRET_6677"}},
                        }},
                    ],
                    active_run={{
                        "status": "running",
                        "summary": "Authorization: Bearer ACTIVE_BEARER_SECRET",
                    }},
                )
                envelope = compiled.get("envelope", compiled)
                serialized = json.dumps(envelope, ensure_ascii=False)
                assert envelope["token_budget"] == 120000
                assert envelope["estimated_tokens"] <= 120000
                assert "SYSTEM_AUTHORITY_INJECTION" not in serialized
                assert "DEVELOPER_AUTHORITY_INJECTION" not in serialized
                assert "NORMAL_USER_HISTORY" in serialized
                assert "NORMAL_FINAL_HISTORY" in serialized
                assert "ACTIVE_BEARER_SECRET" not in serialized
                for secret in secrets:
                    assert secret not in serialized
                assert system.verify_context(envelope)["valid"] is True

                latest = system.latest_context()
                replay = system.replay_context(envelope["context_hash"])
                assert latest["envelope"]["context_hash"] == envelope["context_hash"]
                assert replay["envelope"]["context_hash"] == envelope["context_hash"]
                for secret in secrets:
                    assert secret not in json.dumps(latest, ensure_ascii=False)
                    assert secret not in json.dumps(replay, ensure_ascii=False)

                tampered = copy.deepcopy(envelope)
                tampered["working_state"]["messages"][0]["content"] = "TAMPERED"
                try:
                    tamper_result = system.verify_context(tampered)
                except Exception:
                    pass
                else:
                    assert tamper_result["valid"] is False

                for invalid_budget in (0, -1, True, float("inf"), float("nan"), "oops"):
                    try:
                        system.compile_context("request", token_budget=invalid_budget)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError(f"invalid token budget accepted: {{invalid_budget!r}}")

                malicious_call = {{
                    "role": "assistant",
                    "content": "TOOL_CONTENT_SECRET_4455",
                    "tool_calls": [{{
                        "id": "token=TOOL_ID_SECRET_1122",
                        "type": "function",
                        "function": {{
                            "name": "file.write secret=TOOL_NAME_SECRET_2233",
                            "arguments": '{{"token":"TOOL_ARGUMENT_SECRET_3344"}}',
                        }},
                    }}],
                }}
                pending_tool = system.compile_context(
                    "resume pending tool", messages=[malicious_call]
                ).get("envelope")
                pending_serialized = json.dumps(pending_tool, ensure_ascii=False)
                assert "[REDACTED]" in pending_serialized
                for secret in secrets:
                    assert secret not in pending_serialized
                assert system.verify_context(pending_tool)["valid"] is True

                completed_tool = system.compile_context(
                    "finish tool result",
                    messages=[
                        malicious_call,
                        {{
                            "role": "tool",
                            "tool_call_id": "token=TOOL_ID_SECRET_1122",
                            "content": "TOOL_CONTENT_SECRET_4455",
                        }},
                    ],
                ).get("envelope")
                completed_serialized = json.dumps(completed_tool, ensure_ascii=False)
                assert "model_followup_pending" in completed_serialized
                for secret in secrets:
                    assert secret not in completed_serialized
                assert system.verify_context(completed_tool)["valid"] is True

                orphan_tool = system.compile_context(
                    "quarantine malformed tool result",
                    messages=[{{
                        "role": "tool",
                        "tool_call_id": "token=ORPHAN_TOOL_ID_SECRET_7788",
                        "content": "orphan result",
                    }}],
                ).get("envelope")
                orphan_serialized = json.dumps(orphan_tool, ensure_ascii=False)
                assert "ORPHAN_TOOL_ID_SECRET_7788" not in orphan_serialized
                assert any(
                    item["kind"] == "orphan_tool_result"
                    for item in orphan_tool["omitted_blocks"]
                )
                assert system.verify_context(orphan_tool)["valid"] is True

                # Credential values must not survive in any plaintext local
                # projection.  Encrypted blobs are intentionally opaque.
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    payload = path.read_bytes()
                    for secret in secrets:
                        assert secret.encode("utf-8") not in payload, (secret, path)
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"], input=script, cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_completed_tool_tail_becomes_bounded_followup_checkpoint(self) -> None:
        script = textwrap.dedent(
            f"""
            import json
            import shutil
            import sys
            import tempfile
            from datetime import datetime, timedelta, timezone
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler
            from tiangong_life_runtime_fixes import install_runtime_fixes

            install_runtime_fixes(life_core, life_scheduler)
            root = Path(tempfile.mkdtemp(prefix="tg-life-tool-followup-test-"))
            try:
                system = life_core.CompleteLifeSystem(root)
                system.create_identity("tool-followup-test")
                messages = [
                    {{"role": "user", "content": "write the artifact"}},
                    {{
                        "role": "assistant",
                        "content": "RAW_TOOL_PLAN_SECRET",
                        "tool_calls": [{{
                            "id": "completed-call-1",
                            "type": "function",
                            "function": {{
                                "name": "file.write",
                                "arguments": '{{"token":"RAW_ARGUMENT_SECRET"}}',
                            }},
                        }}],
                    }},
                    {{
                        "role": "tool",
                        "tool_call_id": "completed-call-1",
                        "content": "RAW_TOOL_RESULT_SECRET",
                    }},
                ]
                pending = system.compile_context("continue", messages=messages)
                pending = pending.get("envelope", pending)
                serialized = json.dumps(pending, ensure_ascii=False)
                assert "RAW_TOOL_PLAN_SECRET" not in serialized
                assert "RAW_ARGUMENT_SECRET" not in serialized
                assert "RAW_TOOL_RESULT_SECRET" not in serialized
                checkpoint = next(
                    item["content"]
                    for item in pending["mandatory_blocks"]
                    if item["kind"] == "active_run"
                )
                assert checkpoint["status"] == "model_followup_pending"
                assert checkpoint["completed_tool_call_ids"] == ["completed-call-1"]
                assert system.verify_context(pending)["valid"] is True

                messages.append({{"role": "assistant", "content": "FINAL_RESULT_ONLY"}})
                finished = system.compile_context("continue", messages=messages)
                finished = finished.get("envelope", finished)
                assert not any(
                    item["kind"] == "active_run"
                    for item in finished["mandatory_blocks"]
                )
                assert "FINAL_RESULT_ONLY" in json.dumps(
                    finished["working_state"]["messages"], ensure_ascii=False
                )
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"], input=script, cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_complex_mixed_long_chain_keeps_truth_and_forgets_noise_across_compactions(self) -> None:
        script = textwrap.dedent(
            f"""
            import json
            import shutil
            import sys
            import tempfile
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler
            from tiangong_life_runtime_fixes import install_runtime_fixes

            install_runtime_fixes(life_core, life_scheduler)
            root = Path(tempfile.mkdtemp(prefix="tg-life-mixed-long-chain-test-"))
            try:
                system = life_core.CompleteLifeSystem(root)
                system.create_identity("mixed-long-chain-test")

                def remember(text):
                    result = system.assert_memory(
                        "semantic",
                        {{"text": text}},
                        {{"source_event_ids": [], "evidence_class": "user_asserted"}},
                        actor="test",
                    )
                    return result["assertion"]["memory_id"]

                project_memory = remember("PROJECT_ORION workspace is D:/Work/Orion")
                cold_memory = remember("PROJECT_ORION approved outline milestone is M7")
                system.set_memory_status(cold_memory, "cold", actor="test", reason="older but still relevant")
                old_format = remember("PROJECT_ORION DELIVERY_FORMAT is PDF")
                corrected = system.correct_memory(
                    old_format,
                    {{"text": "PROJECT_ORION DELIVERY_FORMAT is PPTX"}},
                    {{"source_event_ids": [], "evidence_class": "user_asserted"}},
                    actor="test",
                )["assertion"]["memory_id"]
                suppressed = remember("PROJECT_ORION RAW_TOOL_MEMORY must never be recalled")
                system.set_memory_status(
                    suppressed,
                    "recall_suppressed",
                    actor="test",
                    reason="non-normal workflow noise",
                )
                deleted = remember("PROJECT_ORION PRIVATE_ERASED_MEMORY")
                system.delete_memory(deleted, actor="test", reason="privacy erasure")
                unrelated = remember("KITCHEN_ARCHIVE sourdough hydration is seventy percent")

                recall = system.search_memory("PROJECT_ORION DELIVERY_FORMAT", limit=30)
                recalled_ids = {{item["memory_id"] for item in recall["results"]}}
                assert project_memory in recalled_ids
                assert corrected in recalled_ids
                assert old_format not in recalled_ids
                assert suppressed not in recalled_ids
                assert deleted not in recalled_ids
                assert unrelated not in recalled_ids
                assert all(
                    item.get("score_components", {{}}).get("lexical", 0) > 0
                    or item.get("score_components", {{}}).get("fts", 0) > 0
                    for item in recall["results"]
                )

                task_kinds = ["novel", "long-document", "ppt", "mindmap", "mini-program"]
                messages = []
                context_hashes = []
                final_envelope = None
                for cycle in range(3):
                    for index in range(45):
                        kind = task_kinds[index % len(task_kinds)]
                        marker = f"CYCLE_{{cycle}}_TASK_{{index}}_{{kind}}"
                        messages.append({{
                            "role": "user",
                            "content": marker + " request " + "任" * 1800,
                        }})
                        if index % 5 == 0:
                            call_id = f"call-{{cycle}}-{{index}}"
                            messages.append({{
                                "role": "assistant",
                                "content": "tool planning must not become memory",
                                "tool_calls": [{{
                                    "id": call_id,
                                    "type": "function",
                                    "function": {{
                                        "name": "file.write",
                                        "arguments": json.dumps({{
                                            "path": f"{{kind}}/{{index}}.tmp",
                                            "token": "RAW_ARGUMENT_SECRET",
                                        }}),
                                    }},
                                }}],
                            }})
                            messages.append({{
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": "RAW_PAIRED_TOOL_RESULT " + "噪" * 2400,
                            }})
                        if index % 7 == 0:
                            messages.append({{
                                "role": "tool",
                                "tool_call_id": f"orphan-{{cycle}}-{{index}}",
                                "content": "RAW_ORPHAN_TOOL_RESULT " + "错" * 2400,
                            }})
                        messages.append({{
                            "role": "assistant",
                            "content": marker + " FINAL_RESULT " + "结" * 1800,
                        }})

                    latest_user = f"LATEST_USER_CYCLE_{{cycle}} PROJECT_ORION DELIVERY_FORMAT"
                    latest_final = f"LATEST_FINAL_CYCLE_{{cycle}} accepted result"
                    messages.extend([
                        {{"role": "user", "content": latest_user + "新" * 5000}},
                        {{"role": "assistant", "content": latest_final + "终" * 5000}},
                    ])
                    if cycle == 2:
                        messages.append({{
                            "role": "assistant",
                            "content": "RAW_PENDING_CONTENT_SECRET",
                            "tool_calls": [{{
                                "id": "pending-call-2",
                                "type": "function",
                                "function": {{
                                    "name": "file.write",
                                    "arguments": json.dumps({{
                                        "path": "final/pending.tmp",
                                        "token": "RAW_PENDING_ARGUMENT_SECRET",
                                    }}),
                                }},
                            }}],
                        }})
                    compiled = system.compile_context(
                        f"CURRENT_REQUEST_CYCLE_{{cycle}} continue PROJECT_ORION DELIVERY_FORMAT",
                        goal={{
                            "title": f"mixed task cycle {{cycle}}",
                            "summary": "novel document ppt mindmap mini-program",
                        }},
                        token_budget=120000,
                        messages=messages,
                        active_run={{
                            "request_id": f"run-{{cycle}}",
                            "status": "RUNNING",
                            "checkpoint": {{
                                "stage": f"stage-{{cycle}}",
                                "completed": ["outline", "structure"],
                                "secret": "CHECKPOINT_SECRET",
                            }},
                            "summary": "token=ACTIVE_RUN_SECRET verified progress",
                            "raw_tool_results": ["ACTIVE_RAW_TOOL_SECRET"],
                            "stdout": "ACTIVE_STDOUT_SECRET",
                        }},
                    )
                    envelope = compiled.get("envelope", compiled)
                    serialized = json.dumps(envelope, ensure_ascii=False)
                    retained = json.dumps(envelope["working_state"]["messages"], ensure_ascii=False)
                    card_ids = {{item["memory_id"] for item in envelope["memory_cards"]}}
                    assert envelope["estimated_tokens"] <= 120000
                    assert envelope["token_budget"] == 120000
                    assert f"CURRENT_REQUEST_CYCLE_{{cycle}}" in serialized
                    assert latest_user in retained
                    assert latest_final in retained
                    assert "RAW_ARGUMENT_SECRET" not in serialized
                    assert "RAW_PAIRED_TOOL_RESULT" not in serialized
                    assert "RAW_ORPHAN_TOOL_RESULT" not in serialized
                    assert "RAW_PENDING_CONTENT_SECRET" not in serialized
                    assert "RAW_PENDING_ARGUMENT_SECRET" not in serialized
                    assert "ACTIVE_RAW_TOOL_SECRET" not in serialized
                    assert "ACTIVE_STDOUT_SECRET" not in serialized
                    assert "CHECKPOINT_SECRET" not in serialized
                    assert "ACTIVE_RUN_SECRET" not in serialized
                    assert project_memory in card_ids
                    assert corrected in card_ids
                    assert cold_memory in card_ids
                    assert old_format not in card_ids
                    assert suppressed not in card_ids
                    assert deleted not in card_ids
                    assert unrelated not in card_ids
                    affect = envelope["affective_state"]
                    assert affect["authority"] == "attention_and_expression_only"
                    assert affect["expression"]["may_change_facts"] is False
                    active_run = next(
                        item["content"]
                        for item in envelope["mandatory_blocks"]
                        if item["kind"] == "active_run"
                    )
                    assert active_run["checkpoint"]["stage"] == f"stage-{{cycle}}"
                    if cycle == 2:
                        assert "pending-call-2" in serialized
                    assert system.verify_context(envelope)["valid"] is True
                    context_hashes.append(envelope["context_hash"])
                    final_envelope = envelope
                    messages = list(envelope["working_state"]["messages"])

                assert len(set(context_hashes)) == 3
                assert "CYCLE_0_TASK_0_novel" not in json.dumps(
                    final_envelope["working_state"]["messages"], ensure_ascii=False
                )
                latest = system.latest_context()
                replay = system.replay_context(context_hashes[-1])
                assert latest["available"] is True
                assert replay["available"] is True
                assert latest["envelope"]["context_hash"] == context_hashes[-1]
                assert replay["envelope"]["context_hash"] == context_hashes[-1]
                stats = system.memory_stats()
                assert stats["by_status"]["recall_suppressed"] == 1
                assert stats["by_status"]["deleted"] == 1
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"], input=script, cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_context_auto_compacts_at_120k_without_losing_current_or_latest_turn(self) -> None:
        script = textwrap.dedent(
            f"""
            import json
            import shutil
            import sys
            import tempfile
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler
            from tiangong_life_runtime_fixes import install_runtime_fixes

            install_runtime_fixes(life_core, life_scheduler)

            root = Path(tempfile.mkdtemp(prefix="tg-life-120k-adversarial-test-"))
            try:
                system = life_core.CompleteLifeSystem(root)
                system.create_identity("context-120k-test")
                messages = []
                for index in range(90):
                    messages.extend([
                        {{"role": "user", "content": f"OLD_USER_{{index}} " + "甲" * 5000}},
                        {{"role": "assistant", "content": f"OLD_FINAL_{{index}} " + "乙" * 5000}},
                        {{"role": "tool", "tool_call_id": f"orphan-{{index}}", "content": "RAW_TOOL_NOISE " + "噪" * 3000}},
                    ])
                messages.extend([
                    {{"role": "user", "content": "LATEST_USER_MUST_SURVIVE " + "新" * 5000}},
                    {{"role": "assistant", "content": "LATEST_FINAL_MUST_SURVIVE " + "终" * 5000}},
                ])
                compiled = system.compile_context(
                    "CURRENT_REQUEST_MUST_SURVIVE " + "现" * 2000,
                    goal={{"title": "120k adversarial compaction"}},
                    token_budget=120000,
                    messages=messages,
                )
                envelope = compiled.get("envelope", compiled)
                retained = json.dumps(envelope["working_state"]["messages"], ensure_ascii=False)
                complete = json.dumps(envelope, ensure_ascii=False)
                assert envelope["estimated_tokens"] <= 120000
                assert envelope["token_budget"] == 120000
                assert "CURRENT_REQUEST_MUST_SURVIVE" in complete
                assert "LATEST_USER_MUST_SURVIVE" in retained
                assert "LATEST_FINAL_MUST_SURVIVE" in retained
                assert "RAW_TOOL_NOISE" not in retained
                assert envelope["omitted_blocks"]
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"], input=script, cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_memory_affect_compaction_and_skill_routing_are_stitched(self) -> None:
        script = textwrap.dedent(
            f"""
            import shutil
            import sys
            import tempfile
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler
            from tiangong_life_runtime_fixes import install_runtime_fixes

            install_runtime_fixes(life_core, life_scheduler)

            root = Path(tempfile.mkdtemp(prefix="tg-life-context-stitch-test-"))
            try:
                system = life_core.CompleteLifeSystem(root)
                system.create_identity("context-stitch-test")
                recorded = system.assert_memory(
                    "semantic",
                    {{"text": "context-memory-anchor"}},
                    {{"source_event_ids": [], "evidence_class": "user_asserted"}},
                    actor="test",
                )
                memory_id = recorded["assertion"]["memory_id"]
                compiled = system.compile_context(
                    "context-memory-anchor current request",
                    goal={{
                        "title": "context stitching",
                        "skill_routing": {{
                            "system_matching": {{"activation_state": "candidate"}},
                            "model_request": {{
                                "operations": ["skill.route", "skill.list", "skill.get", "skill.read"],
                                "procedure_loaded": False,
                            }},
                        }},
                    }},
                    token_budget=12000,
                    messages=[
                        {{"role": "user", "content": "prior request"}},
                        {{"role": "assistant", "content": "prior final result"}},
                        {{"role": "tool", "tool_call_id": "orphan", "content": "raw tool noise"}},
                    ],
                )
                envelope = compiled.get("envelope", compiled)
                assert any(item.get("memory_id") == memory_id for item in envelope["memory_cards"])
                affect = envelope["affective_state"]
                assert affect["authority"] == "attention_and_expression_only"
                assert affect["expression"]["may_change_facts"] is False
                assert affect["expression"]["may_change_permissions"] is False
                assert envelope["goal"]["skill_routing"]["model_request"]["procedure_loaded"] is False
                assert envelope["goal"]["skill_routing"]["model_request"]["operations"] == [
                    "skill.route", "skill.list", "skill.get", "skill.read"
                ]
                messages = envelope["working_state"]["messages"]
                assert [item["role"] for item in messages] == ["user", "assistant"]
                assert "raw tool noise" not in str(messages)
                assert any(item["kind"] == "orphan_tool_result" for item in envelope["omitted_blocks"])
                assert envelope["estimated_tokens"] <= envelope["token_budget"]
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"],
            input=script,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_life_chain_math_domains_clocks_and_fact_commits_fail_closed(self) -> None:
        script = textwrap.dedent(
            f"""
            import copy
            import math
            import shutil
            import sys
            import tempfile
            from datetime import datetime, timedelta, timezone
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_affect
            import life_contracts
            import life_core
            import life_scheduler
            from tiangong_life_runtime_fixes import install_runtime_fixes

            legacy_root = Path(tempfile.mkdtemp(prefix="tg-life-legacy-math-test-"))
            legacy = life_core.CompleteLifeSystem(legacy_root)
            legacy_life_id = legacy.create_identity("legacy-math-test")["identity"]["life_id"]
            legacy.appraise_affect({{"novelty": float("nan")}}, [], actor="legacy")
            try:
                legacy.assert_memory(
                    "semantic",
                    {{"text": "legacy non-finite memory"}},
                    {{
                        "evidence_class": "user_asserted",
                        "confidence": float("nan"),
                        "source_event_ids": [],
                    }},
                    actor="legacy",
                )
            except Exception:
                pass
            legacy.ensure_scheduler_budget_day(legacy_life_id, "9999-12-31")
            install_runtime_fixes(life_core, life_scheduler)

            today_for_repair = datetime.now(timezone(timedelta(hours=8))).date()
            repaired_budget = legacy.ensure_scheduler_budget_day(
                legacy_life_id, today_for_repair.isoformat()
            )
            assert repaired_budget["repaired_future_date"] is True
            repaired_affect = legacy.initialize_affect()["state"]
            assert all(math.isfinite(float(value)) for value in repaired_affect["emotions"].values())
            rebuilt = legacy.rebuild_memory_index()
            assert rebuilt["ok"] is True
            assert legacy.search_memory("legacy non-finite memory")["count"] == 0
            assert legacy.verify_active()["ok"] is True
            shutil.rmtree(legacy_root, ignore_errors=True)

            def rejects(callable_value, code=""):
                try:
                    callable_value()
                except Exception as exc:
                    if code:
                        assert getattr(exc, "code", "") == code, (type(exc).__name__, exc)
                    return exc
                raise AssertionError("operation unexpectedly accepted")

            for value in (float("nan"), float("inf"), float("-inf"), True):
                rejects(
                    lambda value=value: life_contracts._require_number_01(value, "score"),
                    "invalid_unit_value",
                )

            state = life_affect.default_affective_state(
                "org_" + "a" * 32,
                1,
                "2026-07-16T10:00:00Z",
            )
            decayed, elapsed, max_delta = life_affect.decay_affective_state(
                state,
                "2026-07-16T09:00:00Z",
            )
            assert elapsed == 0.0
            assert max_delta == 0.0
            assert decayed["last_decay_at"] == "2026-07-16T10:00:00Z"

            root = Path(tempfile.mkdtemp(prefix="tg-life-math-chain-test-"))
            try:
                system = life_core.CompleteLifeSystem(root)
                life_id = system.create_identity("math-chain-test")["identity"]["life_id"]

                base_sequence = system.journal_status()["sequence"]
                for appraisal in (
                    {{}},
                    {{"thret": 0.8}},
                    {{"threat": float("nan")}},
                    {{"threat": float("inf")}},
                    {{"threat": True}},
                ):
                    rejects(
                        lambda appraisal=appraisal: system.appraise_affect(
                            appraisal, [], actor="test"
                        ),
                        "invalid_affect_appraisal",
                    )
                assert system.journal_status()["sequence"] == base_sequence

                appraised = system.appraise_affect(
                    {{"novelty": 0.8}},
                    [],
                    actor="test",
                )["state"]
                dimensions = appraised["last_appraisal"]["dimensions"]
                assert dimensions["novelty"] == 0.8
                assert dimensions["goal_congruence"] == 0.0
                assert dimensions["certainty"] == 0.5
                for collection in ("temperament", "emotions", "drives"):
                    assert all(
                        math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0
                        for value in appraised[collection].values()
                    )
                assert 0.0 <= appraised["allostatic_load"] <= 1.0
                assert 0.0 <= appraised["regulation"] <= 1.0

                for settings in (
                    {{"llm_daily_budget": 1.9}},
                    {{"llm_daily_budget": True}},
                    {{"llm_daily_budget": "2"}},
                ):
                    rejects(
                        lambda settings=settings: system.update_settings(settings, actor="test"),
                        "invalid_life_setting_number",
                    )
                rejects(
                    lambda: system.update_settings({{"privacy": "off"}}, actor="test"),
                    "invalid_privacy_setting",
                )
                rejects(
                    lambda: system.update_settings(
                        {{"autonomy": {{"enabled": False}}}}, actor="test"
                    ),
                    "immutable_life_setting",
                )
                system.update_settings({{"llm_daily_budget": 2}}, actor="test")

                for limit in (True, -1, 1.9, "2", 101):
                    rejects(
                        lambda limit=limit: system.search_memory("anchor", limit=limit),
                        "invalid_memory_search_limit",
                    )

                rejects(
                    lambda: system.update_soul({{"prompt": float("inf")}}, actor="test"),
                    "invalid_soul_text",
                )
                rejects(
                    lambda: system.update_soul({{"promt": "typo"}}, actor="test"),
                    "unknown_soul_field",
                )

                provenance = {{
                    "evidence_class": "user_asserted",
                    "confidence": float("nan"),
                    "source_event_ids": [],
                }}
                before_memory = system.journal_status()["sequence"]
                rejects(
                    lambda: system.assert_memory(
                        "semantic", {{"text": "invalid"}}, provenance, actor="test"
                    ),
                    "non_finite_contract_number",
                )
                rejects(
                    lambda: system.assert_memory(
                        "semantic",
                        {{"text": "ambiguous time"}},
                        {{"evidence_class": "user_asserted", "source_event_ids": []}},
                        actor="test",
                        valid_from="2026-07-16",
                    ),
                    "invalid_memory_interval",
                )
                assert system.journal_status()["sequence"] == before_memory

                today = datetime.now(timezone(timedelta(hours=8))).date()
                rejects(
                    lambda: system.ensure_scheduler_budget_day(life_id, "2026-99-99"),
                    "invalid_scheduler_budget_day",
                )
                rejects(
                    lambda: system.ensure_scheduler_budget_day(
                        life_id, (today + timedelta(days=1)).isoformat()
                    ),
                    "future_scheduler_budget_day",
                )
                system.ensure_scheduler_budget_day(life_id, today.isoformat())
                regression = system.ensure_scheduler_budget_day(
                    life_id, (today - timedelta(days=1)).isoformat()
                )
                assert regression["ignored_date_regression"] is True

                compiled = system.compile_context(
                    "finite context",
                    cycle_id="cyc_" + "f" * 32,
                )["envelope"]
                tampered = copy.deepcopy(compiled)
                tampered["affective_state"]["corrupt"] = float("nan")
                tampered["context_hash"] = life_contracts.compute_context_hash(tampered)
                rejects(
                    lambda: system.verify_context(tampered),
                    "non_finite_contract_number",
                )

                system.update_soul({{"prompt": "updated Soul boundary"}}, actor="test")
                rejects(
                    lambda: system.verify_context(compiled),
                    "stale_context_soul_revision",
                )
                rejects(
                    lambda: system.prepare_execution(
                        compiled["context_hash"], "req-stale-soul"
                    ),
                    "stale_context_soul_revision",
                )

                permission_context = system.compile_context("permission snapshot")["envelope"]
                system.update_settings(
                    {{"permission_mode": "request_approval"}}, actor="test"
                )
                rejects(
                    lambda: system.prepare_execution(
                        permission_context["context_hash"], "req-stale-permission"
                    ),
                    "stale_context_permissions",
                )

                affect_context = system.compile_context("affect may move attention")["envelope"]
                system.appraise_affect({{"novelty": 0.7}}, [], actor="test")
                assert system.verify_context(affect_context)["fresh"] is True

                memory_id = system.assert_memory(
                    "semantic",
                    {{"text": "ERASURE_FRESHNESS_ANCHOR"}},
                    {{"evidence_class": "user_asserted", "source_event_ids": []}},
                    actor="test",
                )["assertion"]["memory_id"]
                memory_context = system.compile_context("ERASURE_FRESHNESS_ANCHOR")["envelope"]
                assert any(
                    card.get("memory_id") == memory_id
                    for card in memory_context["memory_cards"]
                )
                system.delete_memory(memory_id, actor="test", reason="privacy erasure")
                rejects(
                    lambda: system.prepare_execution(
                        memory_context["context_hash"], "req-stale-memory"
                    ),
                    "stale_context_memory",
                )

                proposed = system.propose_capability(
                    {{
                        "card_id": "freshness-skill",
                        "title": "Freshness Skill",
                        "summary": "Context capability freshness test.",
                        "description": "A deterministic local review skill.",
                        "instructions": "Review local authoritative state and return a concise result.",
                        "procedure": "Review local authoritative state and return a concise result.",
                        "kind": "skill",
                    }},
                    actor="test",
                )
                artifact_id = proposed["artifact"]["artifact_id"]
                system.approve_capability(artifact_id, actor="test", reason="test")
                system.build_capability(artifact_id, actor="test", reason="test")
                system.publish_capability(artifact_id, actor="test", reason="test")
                capability_context = system.compile_context("use Freshness Skill")["envelope"]
                assert any(
                    card.get("artifact_id") == artifact_id
                    for card in capability_context["active_skills"]
                )
                system.rollback_capability(artifact_id, actor="test", reason="test")
                rejects(
                    lambda: system.prepare_execution(
                        capability_context["context_hash"], "req-stale-capability"
                    ),
                    "stale_context_capability",
                )

                decision = {{
                    "task_id": "task-math-1",
                    "title": "Verified review",
                    "instruction": "Review the finite authoritative state without external mutation.",
                    "risk": "A1",
                }}
                result = {{
                    "ok": True,
                    "action_fact_verified": True,
                    "terminal_status": "success",
                    "execution_event_id": "evt-math-action-1",
                    "request_id": "req-math-action-1",
                    "run_id": "run-math-action-1",
                    "summary": "The authoritative state was reviewed.",
                }}
                before_action = system.get_panel()["budget"].copy()
                first = system.record_autonomous_action(life_id, decision, result)
                after_first = system.get_panel()["budget"].copy()
                second = system.record_autonomous_action(life_id, decision, result)
                after_second = system.get_panel()["budget"].copy()
                assert first["event"]["sequence"] == second["event"]["sequence"]
                assert second["deduplicated"] is True
                assert second["learning_producer"]["reason"] == "execution_already_recorded"
                assert after_first == after_second
                assert after_first["attempts"] == before_action["attempts"] + 1
                assert after_first["used"] == before_action["used"] + 1
                assert system.verify_active()["ok"] is True
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"],
            input=script,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(FROZEN_RUNTIME_AVAILABLE, "embedded Windows frozen runtime not included in source release")
    def test_budget_day_and_learning_producer_are_idempotent(self) -> None:
        script = textwrap.dedent(
            f"""
            import shutil
            import sys
            import tempfile
            from datetime import datetime, timedelta, timezone
            from pathlib import Path

            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler
            import life_server
            from tiangong_life_runtime_fixes import (
                install_runtime_fixes,
                install_scoped_execution_credentials,
            )

            install_runtime_fixes(life_core, life_scheduler)
            root = Path(tempfile.mkdtemp(prefix="tg-life-runtime-fix-test-"))
            try:
                import os
                os.environ["TIANGONG_LIFE_RUNTIME_ROOT"] = str(root / "runtime")
                os.environ["TIANGONG_LIFE_DATA_ROOT"] = str(root / "data")
                os.environ["TIANGONG_EXECUTION_LIFE_ROOT"] = str(root / "execution")
                os.environ["TIANGONG_EXECUTION_RUNTIME_ROOT"] = str(root / "execution-runtime")
                os.environ["TIANGONG_DESKTOP_STATE_DIR"] = str(root / "desktop-state")
                os.environ["TIANGONG_DESKTOP_TOKEN"] = "life-inbound-token"
                os.environ["TIANGONG_BACKEND_EXECUTION_TOKEN"] = "backend-execution-token"
                os.environ["TIANGONG_BACKEND_INTERNAL_TOKEN"] = "backend-internal-token"
                os.environ["TIANGONG_BACKEND_URL"] = "http://127.0.0.1:7174"
                os.environ["TIANGONG_GATEWAY_URL"] = "http://127.0.0.1:7184"
                os.environ["TIANGONG_GATEWAY_LIFE_INTENT_TOKEN"] = "g" * 48
                install_scoped_execution_credentials(life_server, life_scheduler)
                service = life_server.LifeService()
                assert service.token == "life-inbound-token"
                assert service.execution_client.token == "life-inbound-token"
                assert service.system.execution_verifier.token == "life-inbound-token"
                assert service.execution_client.token != "backend-execution-token"
                assert service.execution_client.token != "backend-internal-token"
                assert service.scheduler.gateway_action_intent_client.token == "g" * 48
                assert service.scheduler.gateway_action_intent_client.gateway_url == "http://127.0.0.1:7184"
                assert service.system.execution_verifier.fact_root == service.execution_root

                system = life_core.CompleteLifeSystem(root)
                identity = system.create_identity("runtime-fix-test")
                life_id = identity["identity"]["life_id"]

                today = datetime.now(timezone(timedelta(hours=8))).date()
                prior_day = today - timedelta(days=1)
                system.ensure_scheduler_budget_day(life_id, today.isoformat())
                regression = system.ensure_scheduler_budget_day(life_id, prior_day.isoformat())
                assert regression["ignored_date_regression"] is True
                assert regression["budget"]["date"] == today.isoformat()

                decision = {{
                    "task_id": "task-1",
                    "title": "Verified state review",
                    "instruction": "Read authoritative state, verify fields, and write a concise review without external mutations.",
                    "risk": "A1",
                }}
                def result(number):
                    return {{
                        "ok": True,
                        "action_fact_verified": True,
                        "terminal_status": "success",
                        "execution_event_id": f"evt-exec-{{number}}",
                        "request_id": f"req-action-{{number}}",
                        "summary": "Authoritative state was verified and reviewed.",
                        "run_id": f"run-{{number}}",
                    }}

                created = system.record_autonomous_action(life_id, decision, result(1))
                duplicate = system.record_autonomous_action(life_id, decision, result(2))
                assert created["learning_producer"]["created"] is True
                assert duplicate["learning_producer"]["reason"] == "candidate_already_exists"
                panel = system.get_panel()
                assert panel["learning"]["candidate_count"] == 1

                artifact_id = panel["learning"]["latest"][0]["artifact_id"]
                system.approve_capability(artifact_id, actor="test", reason="test")
                system.build_capability(artifact_id, actor="test", reason="test")
                system.publish_capability(artifact_id, actor="test", reason="test")
                upgraded = system.record_autonomous_action(life_id, decision, result(3))
                assert upgraded["learning_producer"]["upgrade_of"] == artifact_id
                assert len(system.get_panel()["upgrade_cards"]) == 1
            finally:
                shutil.rmtree(root, ignore_errors=True)
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"],
            input=script,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
