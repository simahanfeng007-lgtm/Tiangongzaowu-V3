from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopArtifactCardTests(unittest.TestCase):
    def test_pathless_artifact_card_survives_conversation_state(self) -> None:
        module_url = (ROOT / "app/frontend-v2/renderer/core/state.mjs").as_uri()
        card = {
            "artifact_schema": "tiangong.gateway.artifact-card.v1",
            "gateway_request_id": "req_" + "1" * 64,
            "run_id": "run_" + "2" * 64,
            "generation": 3,
            "artifact_id": "art_" + "3" * 64,
            "artifact_revision_id": "arv_" + "4" * 64,
            "revision": 1,
            "filename": "verified.docx",
            "size_bytes": 4096,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "artifact_kind": "document",
            "format_id": "docx",
            "content_sha256": "5" * 64,
            "manifest_sha256": "6" * 64,
            "qc_state": "PASSED",
            "qc_checks": ["docx-structure@1"],
            "created_at_ms": 1000,
            "open_capability": "gateway_artifact_revision",
            "card_sha256": "7" * 64,
            "path": "C:\\untrusted\\must-not-reach-renderer.exe",
        }
        script = f"""
            import {{ createState }} from {json.dumps(module_url)};
            const values = new Map();
            globalThis.localStorage = {{
              getItem: (key) => values.has(key) ? values.get(key) : null,
              setItem: (key, value) => values.set(key, String(value)),
              removeItem: (key) => values.delete(key)
            }};
            globalThis.window = globalThis;
            const state = createState();
            state.addMessage("assistant", "已生成真实产物", false, {{ attachments: [JSON.parse(process.argv[1])] }});
            const attachment = state.snapshot().messages.at(-1).attachments[0];
            console.log(JSON.stringify(attachment));
        """
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                json.dumps(card, separators=(",", ":")),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        stored = json.loads(completed.stdout)
        self.assertEqual(stored["artifact_revision_id"], card["artifact_revision_id"])
        self.assertEqual(stored["run_id"], card["run_id"])
        self.assertEqual(stored["generation"], 3)
        self.assertEqual(stored["name"], "verified.docx")
        self.assertEqual(stored["path"], "")

    def test_renderer_uses_structured_card_and_dedicated_main_process_ipc(self) -> None:
        conversation = (
            ROOT / "app/frontend-v2/renderer/plugins/conversation-panel.mjs"
        ).read_text(encoding="utf-8")
        runtime = (
            ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs"
        ).read_text(encoding="utf-8")
        actions = (ROOT / "app/frontend-v2/renderer/core/actions.mjs").read_text(
            encoding="utf-8"
        )
        preload = (ROOT / "app/preload.js").read_text(encoding="utf-8")
        main = (ROOT / "app/main.js").read_text(encoding="utf-8")
        styles = (ROOT / "app/frontend-v2/styles/conversation.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('button.dataset.openArtifact = item.artifact_revision_id', conversation)
        self.assertIn("actions.openArtifact?.(artifactButton._tiangongArtifactCard)", conversation)
        self.assertIn('facts.textContent = `QC PASSED', conversation)
        self.assertNotIn("button.dataset.openPath = item.path", conversation)
        self.assertIn("artifact-card-open-status", styles)
        self.assertIn("async function gatewayArtifactCards", runtime)
        self.assertIn("/api/v1/artifacts?request_id=", runtime)
        self.assertIn("async openArtifact(item = {})", runtime)
        self.assertIn("item?.artifact_revision_id || item?.path", actions)
        self.assertIn('openArtifact: (payload) => ipcRenderer.invoke("artifact:open"', preload)

        self.assertIn('handleTrusted("artifact:open"', main)
        self.assertIn('"X-Tiangong-Artifact-Open-Token": ARTIFACT_OPEN_TOKEN', main)
        self.assertIn('await sha256File(targetReal) !== card.content_sha256', main)
        self.assertIn("shell.showItemInFolder(targetReal)", main)
        self.assertIn("ok: true", main)
        self.assertNotIn("shell.openPath(targetReal)", main)
        self.assertNotIn("path: targetReal", main)
        self.assertIn('button.textContent = "定位文件"', conversation)
        self.assertIn('"已在文件夹中选中"', conversation)

    def test_http_runtime_fetches_fact_cards_after_gateway_reply(self) -> None:
        module_url = (
            ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs"
        ).as_uri()
        gateway_request_id = "req_" + "1" * 64
        card = {
            "artifact_schema": "tiangong.gateway.artifact-card.v1",
            "gateway_request_id": gateway_request_id,
            "run_id": "run_" + "2" * 64,
            "generation": 1,
            "artifact_id": "art_" + "3" * 64,
            "artifact_revision_id": "arv_" + "4" * 64,
            "revision": 1,
            "filename": "verified.docx",
            "size_bytes": 4096,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "artifact_kind": "document",
            "format_id": "docx",
            "content_sha256": "5" * 64,
            "manifest_sha256": "6" * 64,
            "qc_state": "PASSED",
            "qc_checks": ["docx-structure@1"],
            "created_at_ms": 1000,
            "open_capability": "gateway_artifact_revision",
            "card_sha256": "7" * 64,
            "path": "C:\\untrusted\\must-not-reach-renderer.exe",
        }
        script = f"""
            globalThis.window = globalThis;
            globalThis.location = {{ protocol: "file:", origin: "null" }};
            const values = new Map();
            globalThis.localStorage = {{
              getItem: (key) => values.has(key) ? values.get(key) : null,
              setItem: (key, value) => values.set(key, String(value)),
              removeItem: (key) => values.delete(key)
            }};
            globalThis.tiangongDesktop = {{
              gatewayUrl: "http://127.0.0.1:7184",
              getGatewayUrl: () => "http://127.0.0.1:7184",
              getGatewayHeaders: () => ({{ "X-Tiangong-Token": "test" }})
            }};
            const card = JSON.parse(process.argv[1]);
            const calls = [];
            globalThis.fetch = async (url, options = {{}}) => {{
              const parsed = new URL(String(url));
              const path = parsed.pathname;
              calls.push(path + parsed.search);
              let payload;
              if (path === "/api/v1/llm/status") {{
                payload = {{ credential_state: "configured" }};
              }} else if (path === "/api/v1/gateway/desktop/inbound") {{
                payload = {{
                  schema: "tiangong.gateway.desktop-inbound-acceptance.v1",
                  ok: true,
                  gateway_request_id: card.gateway_request_id
                }};
              }} else if (path === "/api/v1/gateway/desktop/status") {{
                payload = {{
                  ok: true,
                  gateway_request_id: card.gateway_request_id,
                  run: {{
                    request_id: card.gateway_request_id,
                    gateway_request_id: card.gateway_request_id,
                    status: "COMPLETED",
                    final_response: "已生成"
                  }},
                  events: [],
                  event_cursor: {{ next_seq: 0 }}
                }};
              }} else if (path === "/api/v1/artifacts") {{
                payload = {{
                    schema: "tiangong.gateway.artifact-cards.v1",
                    gateway_request_id: card.gateway_request_id,
                    presentation_request_id: card.gateway_request_id,
                    artifacts: [card]
                }};
              }} else {{
                throw new Error(`unexpected fetch ${{path}}`);
              }}
              return {{ ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(payload) }};
            }};
            const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
            const runtime = createHttpRuntime();
            const result = await runtime.send({{
              message: "生成文档",
              requestId: "frontend-request",
              sessionId: "session-1"
            }});
            console.log(JSON.stringify({{ result, calls }}));
        """
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                json.dumps(card, separators=(",", ":")),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        output = json.loads(completed.stdout)
        self.assertTrue(output["result"]["ok"], output)
        self.assertEqual(
            output["result"]["generated_attachments"][0]["artifact_revision_id"],
            card["artifact_revision_id"],
        )
        self.assertNotIn("path", output["result"]["generated_attachments"][0])
        self.assertTrue(any("/api/v1/artifacts?request_id=" in url for url in output["calls"]))
        self.assertTrue(any("/api/v1/gateway/desktop/inbound" in url for url in output["calls"]))
        self.assertFalse(any("/api/v1/gateway/internal/inbound" in url for url in output["calls"]))


if __name__ == "__main__":
    unittest.main()
