from __future__ import annotations

import threading
import time

from life_service.embedded_runtime import EmbeddedLifeRuntime


def _request(life: EmbeddedLifeRuntime, method: str, path: str, payload=None) -> dict:
    status, body, _ = life.request(method, path, payload)
    assert status == 200, (path, status, body)
    return body


def test_new_life_chain_from_conversation_to_autonomous_and_confirmed_learning(tmp_path):
    """Exercise the complete new-chain state transition without old modules."""

    life = EmbeddedLifeRuntime(data_root=tmp_path / "life-data", runtime_root=tmp_path / "life-runtime", mode="embedded")
    life.set_artifact_action_catalog_provider(lambda: [{"action_id": "web.search", "risk": "A3", "available": True}])
    called = threading.Event()
    try:
        _request(life, "POST", "/api/v1/v3/life/memory/turn", {
            "conversation_id": "chat_e2e", "turn_id": "turn_e2e_001",
            "user_text": "请研究如何让生命记忆在重启后仍可召回。",
            "assistant_text": "我会整理持久化和召回验证方案。",
        })
        search = _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "持久化"})
        assert search["results"] and search["results"][0]["memory_type"] == "episodic"

        def decider(scope: dict) -> dict:
            assert any(item["memory_type"] == "episodic" for item in scope["recent_memories"])
            called.set()
            return {
                "request": "document persistent-memory recall verification",
                "target": "knowledge", "risk_level": "A0",
                "title": "持久化记忆召回验证",
                "learning_plan": ["检查身份隔离", "验证重启后检索"],
                "draft_artifact": {"checklist": ["restart", "search"]},
            }

        life.set_learning_decider(decider)
        _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "e2e"})
        assert called.wait(2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            learning = list(life._scope_state()["learning"].values())
            if learning and learning[0].get("status") == "published":
                break
            time.sleep(0.02)
        scope = life._scope_state()
        auto = next(item for item in scope["learning"].values() if item["target"] == "knowledge")
        assert auto["status"] == "published"
        assert auto["artifact_id"] in scope["knowledge"]
        assert auto["learning_execution"]["status"] in {"completed", "completed_with_warnings"}
        assert auto["execution"]["artifact"]["learning_evidence"]["evidence_sha256"]

        skill = _request(life, "POST", "/api/v1/v3/life/learning/draft", {
            "decision": {
                "request": "build a memory-recall diagnostic skill", "target": "skill", "risk_level": "A0",
                "title": "记忆召回诊断",
                "draft_artifact": {
                    "content": "# 记忆召回诊断",
                    "required_actions": ["web.search"],
                    "steps": [{"step_id": "query", "action_id": "web.search", "arguments_template": {"query": "memory recall"}}],
                },
            },
        })["learning"]
        assert skill["risk_level"] == "A3" and skill["status"] == "awaiting_user"
        assert skill["learning_execution"]["status"] in {"completed", "completed_with_warnings"}
        published = _request(life, "POST", "/api/v1/v3/learning/confirm", {
            "learning_id": skill["learning_id"], "draft_sha256": skill["draft_sha256"],
        })
        assert published["learning"]["status"] == "published"
        assert published["learning"]["artifact_id"] in life._scope_state()["capabilities"]
        overlay = _request(life, "GET", "/api/v1/v3/life/capabilities/overlay")
        assert overlay["active_skill_count"] == 0
        assert overlay["pending_activation_count"] == 1
        assert overlay["artifacts"][0]["artifact_id"] == published["learning"]["artifact_id"]
        assert overlay["artifacts"][0]["activation_status"] == "pending"
        assert overlay["model_context"] == []
        _request(life, "POST", "/api/v1/v3/life/capability/activate", {
            "artifact_id": published["learning"]["artifact_id"],
        })
        overlay = _request(life, "GET", "/api/v1/v3/life/capabilities/overlay")
        assert overlay["active_skill_count"] == 1
        assert overlay["model_context"][0]["artifact_id"] == published["learning"]["artifact_id"]
    finally:
        life.close()
