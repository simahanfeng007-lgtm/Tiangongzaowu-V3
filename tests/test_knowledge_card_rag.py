from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from total_gateway.embedded_backend import EmbeddedBackendRuntime
from total_gateway.orchestration import _model_safe_knowledge_reference
from v3 import knowledge_store


def test_import_builds_structured_llm_card_and_searches_it(tmp_path: Path) -> None:
    source = tmp_path / "aurora.md"
    source.write_text(
        "# 极光计划\n\n极光计划使用离线优先同步。冲突处理采用版本向量，审计记录必须长期保留。",
        encoding="utf-8",
    )
    knowledge_store.set_card_enricher(lambda material: {
        "title": "极光计划知识卡",
        "summary": "极光计划是一套离线优先、保留审计记录的同步方案。",
        "key_points": ["使用离线优先同步", "采用版本向量解决冲突", "长期保留审计记录"],
        "keywords": ["极光计划", "离线优先", "版本向量", "审计记录"],
        "outline": ["目标", "同步策略", "审计要求"],
        "content_extract": "冲突处理采用版本向量。",
    })
    try:
        imported = knowledge_store.import_knowledge({
            "knowledgeRoot": str(tmp_path / "knowledge"),
            "workspace": str(tmp_path),
            "paths": [str(source)],
        })
        assert imported["ok"] is True
        doc = imported["imported"][0]
        assert doc["card"]["schema"] == "tiangong.v3.knowledge.card.v1"
        assert doc["card"]["extraction_status"] == "completed"
        assert doc["card"]["extractor"] == "llm"
        assert doc["summary"].startswith("极光计划是一套")
        assert doc["key_points"][1] == "采用版本向量解决冲突"

        searched = knowledge_store.search_knowledge({
            "knowledgeRoot": str(tmp_path / "knowledge"),
            "query": "版本向量",
        })
        assert searched["ok"] is True
        assert searched["cards"][0]["document_id"] == doc["document_id"]
        assert any(match.get("citation_id") == "CARD" for match in searched["cards"][0]["matches"])
    finally:
        knowledge_store.set_card_enricher(None)


def test_import_survives_llm_failure_with_deterministic_card(tmp_path: Path) -> None:
    source = tmp_path / "fallback.txt"
    source.write_text("本文件说明离线索引失败时仍需保留基础摘要和可检索片段。", encoding="utf-8")

    def fail(_material):
        raise RuntimeError("model unavailable")

    knowledge_store.set_card_enricher(fail)
    try:
        imported = knowledge_store.import_knowledge({
            "knowledgeRoot": str(tmp_path / "knowledge"),
            "workspace": str(tmp_path),
            "paths": [str(source)],
        })
        assert imported["ok"] is True
        card = imported["imported"][0]["card"]
        assert card["extraction_status"] == "fallback"
        assert card["extractor"] == "deterministic"
        assert "model unavailable" in card["extraction_error"]
        assert card["summary"]
    finally:
        knowledge_store.set_card_enricher(None)


def test_configured_root_is_shared_by_ui_import_and_backend_retrieval(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime" / "state"
    selected_root = tmp_path / "user-selected-knowledge"
    monkeypatch.setenv("TIANGONG_DESKTOP_STATE_DIR", str(state_dir))
    monkeypatch.delenv("TIANGONG_KNOWLEDGE_DIR", raising=False)
    monkeypatch.delenv("TIANGONG_DESKTOP_KNOWLEDGE_ROOT", raising=False)

    configured = knowledge_store.configure_knowledge({"knowledgeRoot": str(selected_root)})

    assert configured["ok"] is True
    assert configured["configured"] is True
    assert knowledge_store.knowledge_root({}) == selected_root.resolve()


def test_remove_deletes_only_the_owned_knowledge_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("A reusable knowledge record.", encoding="utf-8")
    root = tmp_path / "knowledge"
    imported = knowledge_store.import_knowledge({"knowledgeRoot": str(root), "paths": [str(source)]})
    doc = imported["imported"][0]
    stored = Path(doc["stored_path"])
    assert stored.is_file()

    removed = knowledge_store.remove_knowledge({
        "knowledgeRoot": str(root),
        "document_id": doc["document_id"],
    })

    assert removed["ok"] is True
    assert removed["removed_stored_file"] is True
    assert not stored.exists()
    assert source.is_file()


def test_chat_inbound_injects_caller_cards_and_has_backend_retrieval_fallback() -> None:
    observed: list[dict] = []
    searches: list[dict] = []

    class Module:
        @staticmethod
        def _safe_bridge_json(value, *, source):
            assert source == "chat"
            return value

        @staticmethod
        def _knowledge_action(action, payload):
            assert action == "search"
            searches.append(dict(payload))
            return {
                "ok": True,
                "cards": [{
                    "document_id": "doc_auto",
                    "title": "自动检索卡",
                    "summary": "后端自动检索结果",
                    "matches": [{"citation_id": "C0001", "text": "证据片段"}],
                }],
            }

    class Bridge:
        @staticmethod
        def chuli_duihua(text, user, context):
            observed.append({"text": text, "user": user, "context": context})
            return {"huifu": "ok"}

    backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    backend._module = Module()
    backend.qiaojie = Bridge()
    backend._life_skill_overlay_provider = None

    supplied = [{"document_id": "doc_ui", "summary": "前端命中的知识卡"}]
    backend._inbound({"text": "查询资料", "knowledge_references": supplied})
    assert observed[-1]["context"]["knowledge_references"] == supplied
    assert observed[-1]["context"]["knowledge_retrieval"]["source"] == "caller"
    assert searches == []

    backend._inbound({"text": "自动查知识", "knowledge_root": "C:/knowledge-test"})
    assert searches[-1]["knowledgeRoot"] == "C:/knowledge-test"
    assert observed[-1]["context"]["knowledge_references"][0]["document_id"] == "doc_auto"
    assert observed[-1]["context"]["knowledge_retrieval"]["source"] == "backend_auto"


def test_gateway_knowledge_projection_removes_host_paths_but_keeps_evidence() -> None:
    projected = _model_safe_knowledge_reference({
        "document_id": "doc_safe",
        "title": "安全知识卡",
        "summary": "保留可验证简介。",
        "file_path": "C:/secret/source.md",
        "stored_path": "C:/secret/store/source.md",
        "key_points": ["要点一"],
        "keywords": ["关键词"],
        "matches": [{
            "citation_id": "C0001",
            "text": "可供模型引用的证据。",
            "file_path": "C:/secret/source.md",
        }],
    })
    assert projected["document_id"] == "doc_safe"
    assert projected["matches"][0]["text"] == "可供模型引用的证据。"
    assert "file_path" not in projected
    assert "stored_path" not in projected
    assert "file_path" not in projected["matches"][0]
