from __future__ import annotations

from life_service.memory_lifecycle import advance_lifecycle, initial_lifecycle, recall_lifecycle
from life_service.embedded_runtime import EmbeddedLifeRuntime


def _record() -> dict:
    lifecycle = initial_lifecycle(
        classification={"retention_class": "CHECKPOINT"},
        content={"text": "DeepSeek API settings and user preference"},
        priority=0,
        confidence_milli=800,
        at_ms=1,
    )
    return {
        "classification": {"retention_class": "CHECKPOINT"},
        "content": {"text": "DeepSeek API settings and user preference"},
        "priority": 0,
        "confidence_milli": 800,
        "lifecycle": lifecycle,
    }


def test_lifecycle_freezes_low_heat_memory_and_cue_reactivates_it():
    record = _record()
    lifecycle, changed = advance_lifecycle(record, at_ms=90 * 86_400_000)
    assert changed is True
    assert lifecycle["state"] == "frozen"
    record["lifecycle"] = lifecycle
    lifecycle, changed, thawed = recall_lifecycle(record, query="DeepSeek API", at_ms=90 * 86_400_000 + 1)
    assert changed is True
    assert thawed is True
    assert lifecycle["state"] == "active"
    assert lifecycle["recall_count"] == 1


def test_conversation_turn_and_causal_neighbor_recall_share_new_memory_authority(tmp_path):
    life = EmbeddedLifeRuntime(data_root=tmp_path / "life-data", runtime_root=tmp_path / "life-runtime", mode="embedded")
    try:
        status, turn, _ = life.request("POST", "/api/v1/v3/life/memory/turn", {
            "turn_id": "turn_001", "conversation_id": "session_001",
            "user_text": "请记住 API 设置。sk-abcdefghijklmnop",
            "assistant_text": "我会把 API 设置用于后续连接。",
        })
        assert status == 200 and turn["assertion"]["memory_type"] == "episodic"
        assert "sk-" not in str(turn["assertion"]["content"])
        for memory_id, content in (("mem_cause", {"text": "gateway listens on port 7184"}), ("mem_effect", {"text": "connection becomes available"})):
            assert life.request("POST", "/api/v1/v3/life/memory/assert", {"memory_id": memory_id, "content": content})[0] == 200
        assert life.request("POST", "/api/v1/v3/life/memory/relation", {
            "source_memory_id": "mem_cause", "kind": "causes", "target_memory_id": "mem_effect",
        })[0] == 200
        status, result, _ = life.request("POST", "/api/v1/v3/life/memory/search", {"query": "port 7184"})
        assert status == 200
        rows = {item["memory_id"]: item for item in result["results"]}
        assert rows["mem_cause"]["retrieval_path"] == "direct"
        assert rows["mem_effect"]["retrieval_path"] == "causal_neighbor"
    finally:
        life.close()
