from __future__ import annotations

import atexit
import shutil
from pathlib import Path


def _finish_main_qc_integration() -> None:
    root = Path.cwd()
    store = root / "src/life_service/store.py"
    mirror = root / "app/life-service/runtime314/life_service/store.py"
    if store.exists():
        text = store.read_text(encoding="utf-8")
        old = (
            "            (17, _P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, "
            "_P17_MEMORY_WORLD_CANDIDATE_SHA256),\n"
            "        )\n"
            "        if len(migration) != len(expected_migrations)"
        )
        new = (
            "            (17, _P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, "
            "_P17_MEMORY_WORLD_CANDIDATE_SHA256),\n"
            "            (18, _P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID, "
            "_P18_ACTION_IMPACT_SOURCE_INDEX_SHA256),\n"
            "        )\n"
            "        if len(migration) != len(expected_migrations)"
        )
        if old in text:
            text = text.replace(old, new, 1)
            store.write_text(text, encoding="utf-8", newline="\n")
            shutil.copyfile(store, mirror)
        elif "_P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID" not in text:
            raise RuntimeError("LifeShadowStore.health v18 ledger anchor missing")

    # The production transport must stay fail-closed.  Adapt only the legacy
    # deadline unit-test mock so it reaches the wall-clock guard through the
    # newly introduced pinned-connect contract.
    test = root / "tests/test_simple_chain_loop_budget.py"
    if test.exists():
        source = test.read_text(encoding="utf-8")
        old_test = '''            mock.patch(
                "v3.jineng.model_transport_executor.validate_model_endpoint"
            ),
            mock.patch(
                "v3.jineng.model_transport_executor.time.perf_counter",
'''
        new_test = '''            mock.patch(
                "v3.jineng.model_transport_executor.validate_model_endpoint"
            ),
            mock.patch(
                "v3.jineng.model_transport_executor.pin_model_request",
                return_value=mock.Mock(
                    url="https://93.184.216.34/v1/chat/completions",
                    host_header="api.deepseek.com",
                    sni_hostname="api.deepseek.com",
                    resolved_ip="93.184.216.34",
                ),
            ),
            mock.patch(
                "v3.jineng.model_transport_executor.time.perf_counter",
'''
        if old_test in source:
            test.write_text(source.replace(old_test, new_test, 1), encoding="utf-8", newline="\n")
        elif "model_transport_executor.pin_model_request" not in source:
            raise RuntimeError("deadline transport test mock anchor missing")


atexit.register(_finish_main_qc_integration)
