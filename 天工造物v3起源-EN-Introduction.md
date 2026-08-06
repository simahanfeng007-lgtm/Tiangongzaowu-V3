# Tiangong Zaowu v3.0.3 Complete — Engineering Organism Desktop Product

> An **engineering organism** living on your computer: not merely a chat assistant, but a desktop life system with its own identity, memory, emotions, and autonomy — one that reflects, iterates, proactively communicates with you, and operates a complete set of "body functions".

Product codename: `engineering-organism-v3.0-complete` · Platform: Windows 10/11 x64 · Architecture: Electron desktop + single-process life kernel (`127.0.0.1:7184`)

---

## System Landscape

Tiangong Zaowu is carried by a **single-process gateway**: the LifeKernel, the Omni Body skill executor, the communication modules, and the policy/security engines all run inside one physical process, sharing the state store, the writer lease, and the event chain. The Electron desktop provides the chat, knowledge, skills, body, life, and settings panels.

```
┌─────────────────────────── Electron Desktop ──────────────────────────┐
│ Chat | Knowledge | Skills | Runtime Status | Body/VRM | Life | Settings│
└──────────────┬───────────────────────────────────────────────────────┘
               │ 127.0.0.1:7184
┌──────────────▼─────────────── Single-process Gateway ─────────────────┐
│ Life System (LifeKernel) · Omni Body Skills · Communication (WeChat/  │
│ Feishu) · Policy & Authorization · Security Sandbox · Artifacts &     │
│ Object Store · Orchestration · Contracts & Identity                   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 1. Life System (LifeKernel)

The authoritative implementation lives in `src/life_service/` (40+ modules), with a byte-identical packaged mirror at `app/life-service/runtime314/`. This is the core of the product: a **persistently living** life, not a conventional backend that reacts only when invoked.

### Life State & Life Chain

- Independent identity (`life_id`) and Soul configuration, with live states such as “alive / growth / focus”.
- Every heartbeat, task, and reflection is written into the **Life Chain (state timeline)** — a hash-signed, tamper-evident event journal; any modification is detectable.
- The life panel projects state in real time: today’s status, completed actions, model budget, next heartbeat, current focus, and more.

### Life Mailbox

- The life proactively delivers messages (unread badges and a message list).
- One-click “open mailbox and enter chat” writes the life’s message into the main conversation.
- Proactive sharing is constrained by permissions and quiet hours — it never bypasses message boundaries.

### Identity, Memory & Context

- Identity migration (`identity_migration`): safe migration from legacy/frozen runtimes; legacy data remains auditable, replayable, and cutover-ready.
- Memory (`memory_classification` / `memory_lifecycle` / `memory_migration`): classified management, lifecycle, and migration with encrypted storage.
- Context (`context_api` / `context_authority`): compilation, authorization, and projection — the life knows who it is, what happened, and what it is doing.

### Autonomy & Schedule

- The heartbeat scheduler drives the life cycle every 30 seconds by default (`complete_scheduler`).
- Autonomous task generation and execution (`autonomous_tasks`): the life proposes and runs candidate tasks based on its own state and selected activities, constrained by activity scope (`activity_scope`) and model budget.
- Autonomy level is configurable; failed tasks can recover and resume.

### Reflection, Iteration & Self-Produced Capabilities

- Reflection (`reflection`): periodic review of its own behaviors and outcomes.
- Capability self-learning (`capability_learning` / `learning_executor`) with a capability lifecycle (`capability_lifecycle`): it grows new skills by itself.
- Artifacts (`artifact_executor`): actions produce traceable artifacts.

### Affect, Temperament & Viability

- Emotion system (`affect` / `transient_affect` / `affect_expression`) plus a stable temperament (`temperament`).
- Viability assessment (`viability`) and state projection (`panel_projection`).

### Lifecycle & Safe Cutover

- Single writer lease: only one authoritative writer exists at a time.
- Shadow mode: read-only observation by default (`OBSERVE_ONLY`).
- Cutover requires a signed handoff and evidence that the old writer stopped; rollback verifies the event list before proceeding.
- Legacy compatibility (`legacy_adapter` / `legacy_fusion` / `replay`) closes the migration loop.

---

## 2. Total Gateway & Orchestration

`src/total_gateway/` is the single-process “brain stem” listening on `127.0.0.1:7184`, embedding Runtime, Life, Communication, and Policy:

- **Orchestration (`orchestration` / `coordination`)**: unified scheduling with events and lease coordination.
- **Execution engine (`execution_engine` / `effects`)**: result write-back and side-effect control.
- **Artifact system (`object_store` / `artifact_*`)**: object store plus artifact ingress/QC (`artifact_qc`, `docx_qc`)/egress (`artifact_egress`)/open (`artifact_open`).
- **Policy engine (`policy_engine` / `policy_evidence`)**: authorization, evidence, tickets/grants (`tickets` / `grant_signer`).
- **Tasks & continuity (`active_requests` / `continuity` / `response_saga`)**: long-running task checkpoints, recovery, and SAGA consistency.
- **Skill governance (`skill_authority` / `skill_selection` / `skill_api`)**: skill admission, selection, and invocation.
- **Fact ledger (`fact_ledger`)**: cross-system fact records.
- **Readiness & diagnostics (`readiness_collector` / `diagnostics`)**: health probes and run observation.
- **Soul backup (`soul_backup`)**: encrypted Soul Backup.
- **Object GC (`object_gc`)**: object store lifecycle management.

---

## 3. Omni Body Skill System (Runtime)

The execution layer lives in `app/backend/tiangong-backend/v3/` — the life’s “body functions”:

- **Fact Kernel (`fact_kernel`)**: knowledge/fact foundation, linked with the knowledge-base system.
- **Observation & Assessment (`guancha_pinggu`)**: `observe(guancha)` and `assess(pinggu)` on self and external input.
- **Gutong Layer (`gutong`)**: cross-layer context (`shangxiawen`) and soul loading (`soul_jiazai`).
- **Skill Layer (`jineng`)**: tool execution via `jirou_ceng` (muscle layer), model adapters (`moxing_shipei`: DeepSeek / MiniMax / Google, etc.), HTTP client (`http_kehuduan`), L4 optimization observation, `omni_grant_client`.
- **Evolution System (`jinhua`)**: closed-loop engine (`bihuan_yinqing`), candidate generation (`houxuan_shengcheng`), assessment (`pinggu`), validation/approval (`yanzheng_shenpi`), execution (`zhixing`), meta-language mapping (`yuanyu_yingshe`), expression routing (`biaoda_router`), and math bridging (`shuxue_qiaojie`) — the life can evolve itself.
- **Immunity & Metabolism (`mianyi_daixie`)**: `immunity(mianyi)`, `metabolism(daixie)`, and `experiment(shiyan)`.
- **Governance (`zhili`)**: `security(anquan)`, `version migration(banben_qianyi)`, and `capability registration(nengli_zhuche)`.
- **Self-Healing (`ziyu`)**: engine (`yinqing`) for automatic recovery from anomalies.
- **Master Scheduler (`zongdiaodu`)**: unified scheduling of body functions.
- **Bundled Skills (`bundled_skills`)**: `omni_body_skill` (documents / PPT / spreadsheets / professional apps / CLI / sandbox tool contracts) and `novel-creation` (end-to-end web novel workflow).

Other execution capabilities: `knowledge_store`, `context_compactor`, context continuation (`shangxiawen_xujie`), dialog bridging (`duihua_qiaojie`), reply sanitization (`reply_sanitizer`), voice output (`voice_output`), body state (`shenti_zhuangtai`), body settings (`body_settings`), safe I/O (`safe_io`), permission settings, endpoint security, confirmation store, model stream config, workspace settings, state sync (`zhuangtai_tongbu`), and `quanzhuixian`.

Low-level kernel primitives (`tiangong_kernel/l0_primitives`): autonomy, context, decision, event, health, identity, learning, lifecycle, memory, message, metric, observation, retrieval, time, value.

---

## 4. Communication System

`src/communication_service/` handles multi-channel access and delivery:

- **WeChat**: login (`wechat_login`), inbound/outbound (`wechat_inbound` / `wechat_text_outbound`), attachments and media (`wechat_attachment` / `wechat_media` / `wechat_file_outbound`), sessions (`wechat_session`), transfer control (`wechat_transfer_control`), and worker (`wechat_worker`).
- **Feishu**: inbound/outbound (`feishu_inbound` / `feishu_outbound`), attachments (`feishu_attachment`), routing (`feishu_route`), and worker (`feishu_worker`).
- **Channel governance**: channel authority (`channel_authority`), delivery ledger (`delivery_ledger`), delivery dispatcher (`delivery_dispatcher`), credential vault (`credential_vault`), raw inbound store (`raw_inbound_store`), attachment quarantine (`attachment_quarantine`), inbox (`inbox`), shadow mirror (`shadow_mirror`), production ingress (`production_ingress`), and drain (`drain`).

---

## 5. Conversation System

- Conversation panel (`conversation-panel.mjs`) and history (`history-block.mjs`).
- Model stream config (`model_stream_config`), dialog bridging (`duihua_qiaojie`), and reply sanitization (`reply_sanitizer`).
- Casual chat and task completion are treated as normal terminal states — no false “task stopped” notices.
- Messages, history, and life-mailbox content are unified in one conversation flow.

---

## 6. Knowledge System

- Knowledge panel (`knowledge-panel.mjs`): locate, open, auto-organize, search, and document lists.
- Backend knowledge store (`knowledge_store`) with the Fact Kernel (`fact_kernel`).
- Documents become searchable, organizable knowledge assets shared by the life and the user.

---

## 7. Skills System

- Skills panel (`skills-panel.mjs` / `skills-side-block.mjs`).
- Skill routing index (`skill_router_index.json`, 34 built-in skills), skill authority (`skill_authority`), and skill selection (`skill_selection`).
- Bundled skills: Omni Body and novel creation.
- Capability registration (`nengli_zhuche`) and capability manifest (`capability_manifest.generated.json`).

---

## 8. Runtime Status & Execution System

- Execute panel (`execute-panel.mjs`) and runtime status block (`runtime-status-block.mjs`).
- Run observation (`run_observation`), diagnostics (`diagnostics`), and artifact QC.
- Long-running task execution, checkpoint recovery, and result write-back (`execution_engine` / `response_saga` / `continuity`).

---

## 9. VRM Body & Avatar System

- Body panel (`body-panel.mjs`), avatar panel (`avatar-panel.mjs`), and VRM inspector (`vrm-inspector-panel.mjs`).
- 3D avatar engine (`three-vrm-engine.mjs`): natural stance calibrated to human biomechanics (JOSR 2022 / Lee & Jung 2014), expressions, lip sync, gestures, and real-time driving.
- Shoulder calibration (`shoulder-calibrator.mjs`) and the VRM alignment skill (`.codex/skills/vrm-alignment/`).
- Avatar assets: `捏脸.html` (face editor), `桌面宠物.html` (desktop pet), VRM import, and VRC-to-VRM conversion (`vrc-import.js`), plus avatar asset/storage hosts.
- Model memory: the last selected model is remembered across restarts.

---

## 10. Settings & Model Access System

- Settings panel (`settings-panel.mjs`) with provider presets (`provider-presets.mjs`).
- Model endpoint binding and API probes (`endpoint_security` / `model_endpoint`), credentials stored encrypted locally.
- Body settings, workspace settings, permission settings, and model stream config.
- Six themes, voice, avatar, and WeChat/Feishu bindings.

---

## 11. Security & Sandbox System

- `src/runtime_security/`: DPAPI credential protection (`dpapi`), model endpoint validation (`model_endpoint`), ticket verification (`ticket_verification`), and safe archive (`archive`).
- Python/Shell sandboxes: private workspaces, sanitized environments, resource limits, and atomic write-back (`safe_io` / `sandbox_runtime` / Windows AppContainer).
- Permission settings (`permission_settings`) and confirmation store (`confirmation_store`).
- Policy engine (`policy_engine`) with A5 hard refusal: sensitive operations are rejected by deterministic policy and cannot be bypassed by the model.
- Reply sanitization (`reply_sanitizer`) and endpoint security (`endpoint_security`).
- Encrypted Soul Backup and signed event-chain verification.

---

## 12. Contracts & Identity System

`src/contracts/` defines a full set of machine-verifiable contracts:

- Life (`life`), memory (`memory`), agency (`agency`), affect (`affect`), causal (`causal`).
- Delivery (`delivery` / `delivery_authorization`), execution (`execution`), artifacts (`artifacts`).
- Policy (`policy`), security (`security`), authorization (`authorization`), identities (`identities`), scope (`scope`).
- Shadow (`shadow`), cutover (`cutover`), readiness (`readiness`), release (`release`), reliability (`reliability`), state machine (`state_machine`).
- Primary contracts: `tiangong.life.api.v2`, `tiangong.desktop.backend.v3`, `tiangong.communication.api.v1`, `tiangong.total-gateway.api.v1`.

---

## 13. Update & Trust System

- Secure updater (`secure-updater.js`) with an update trust root (`update-trust.json`, Ed25519 public-key binding).
- Updates verify signature and trust root before applying; failures roll back automatically.
- Online updates are disabled by default and only enabled after configuring a real public key, an HTTPS update source, and a Windows signed publisher.

---

## 14. Release Pipeline

- `scripts/release-win.mjs`: freezes the single-process gateway (PyInstaller), embeds the backend source tree, and runs Runtime / Life / Communication / Policy contract probes.
- electron-builder + NSIS produce the installer (`npm run release:win`).
- `verify-windows-artifacts.ps1` re-extracts the installer and re-verifies the release binding, app.asar VRM module closure, forbidden assets, and hash consistency.
- Without a signing certificate, artifacts are explicitly marked as “unsigned candidate packages”.

---

## 15. Desktop Shell & Process Lifecycle

- `main.js`: Electron main process and window management.
- `service-supervisor.js`: starts and supervises the single-process gateway.
- `runtime-root.js`: runtime root and path resolution.
- `preload.js`: sandboxed preload (electron-only dependency) with a narrow IPC bridge.
- Single-instance epoch, local state store, life writer lease, and module readiness checks.

---

## Quick Start (Source)

Prepare the bundled Python runtime on first run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-source.ps1
```

Start:

```powershell
.\scripts\start-source.ps1
```

(Or double-click `start-tiangong.bat`)

Verify the gateway and life kernel are ready:

```powershell
Invoke-RestMethod http://127.0.0.1:7184/ready | ConvertTo-Json -Depth 10
```

Expect `status: READY`, `deployment_mode: embedded`, and `embedded_modules.life.life_ready = true`.

Run tests:

```powershell
.\scripts\verify-source.ps1 -Full
```

---

## Building the Windows Installer

```powershell
cd app
npm run release:win
```

The pipeline freezes the single-process gateway, runs Runtime / Life / Communication / Policy contract probes (the build is blocked if the life API contract fails), and produces an installer via electron-builder + NSIS. Without a signing certificate, the artifact is explicitly marked as an “unsigned candidate package”.

---

## Directory Overview

```
app/                          Electron desktop, frontend, main backend, life service, bundled runtime
src/life_service/             Authoritative life system (identity/memory/context/schedule/autonomy/reflection/learning)
src/total_gateway/            Single-process gateway (Runtime/Life/Communication/Policy embedded)
src/communication_service/    Communication modules (WeChat, Feishu, etc.)
src/runtime_security/         Security and credentials (DPAPI/model endpoint/tickets)
src/contracts/                Full machine-verifiable contract set
app/backend/tiangong-backend/ Omni Body execution layer (fact/observation/gutong/skills/evolution/immunity/governance/self-healing)
app/life-service/runtime314/  Byte-identical packaged life runtime mirror
app/frontend-v2/              Frontend (chat/knowledge/skills/body/life/settings)
tests/                        Top-level regression tests
scripts/                      Source setup/start/verify/release pipelines
.codex/skills/vrm-alignment/  VRM biomechanical alignment skill (stance/hand calibration workflow and data)
```

---

## Version Info

- Product: Tiangong Zaowu v3.0.3 Complete
- Architecture version: `engineering-organism-v3.0-complete`
- Life API contract: `tiangong.life.api.v2`
- Source baseline: `2026-07-22-single-process-continuity-portability-50-final`
