# Tiangong Zaowu v3.0.3 Complete — Engineering Organism Desktop Product

> An **engineering organism** living on your computer: not merely a chat assistant, but a desktop life system with its own identity, memory, emotions, and autonomy — one that reflects, iterates, and proactively communicates with you.

Product codename: `engineering-organism-v3.0-complete` · Platform: Windows 10/11 x64 · Architecture: Electron desktop + single-process life kernel

---

## Core: The Life System

Tiangong Zaowu embeds a resident **LifeKernel**. It lives inside the single-process gateway, starts with the app, and is heartbeat-driven — it keeps living, instead of acting only when invoked like a conventional backend.

### Life State & Life Chain

- Each life has its own identity (`life_id`) and Soul configuration, with live states such as “alive / growth / focus”.
- Every heartbeat, task, and reflection is written into the **Life Chain (state timeline)** — a hash-signed, tamper-evident event journal; any modification is detectable.
- The life panel projects state in real time: today’s status, completed actions, model budget, next heartbeat, current focus, and more.

### Life Mailbox

The life is not a one-way answering tool. It proactively delivers messages to the user when it has something to say:

- Unread badges and a message list;
- One-click “open mailbox and enter chat”, writing the life’s message into the main conversation;
- Proactive sharing is constrained by permissions and quiet hours — it never bypasses message boundaries.

### Identity, Memory & Context

- **Identity & migration**: each life has its own identity store and can safely migrate from legacy/frozen runtimes (`identity_migration`); legacy data remains auditable, replayable, and cutover-ready.
- **Memory**: classified memory (`memory_classification`), memory lifecycle (`memory_lifecycle`), and migration (`memory_migration`), with encrypted storage.
- **Context**: context compilation, authorization, and projection (`context_api` / `context_authority`) — the life knows who it is, what happened, and what it is doing.

### Autonomy & Schedule

- The heartbeat scheduler drives the life cycle every 30 seconds by default (`complete_scheduler`).
- Autonomous task generation and execution (`autonomous_tasks`): the life proposes and runs candidate tasks based on its own state and selected activities, constrained by activity scope (`activity_scope`) and model budget.
- Autonomy level is configurable; failed tasks can recover and resume — a single failure does not “kill” the life.

### Reflection, Iteration & Self-Produced Capabilities

- **Reflection (`reflection`)**: the life periodically reviews its own behaviors and outcomes.
- **Capability self-learning (`capability_learning` / `learning_executor`)**: the life can learn new capabilities and manage them through a capability lifecycle (`capability_lifecycle`) — it grows new skills by itself.
- **Artifacts (`artifact_executor`)**: actions produce traceable artifacts.

### Affect & Temperament

- Emotion system (`affect` / `transient_affect` / `affect_expression`) plus a stable temperament (`temperament`) make its expression emotionally nuanced rather than mechanical.

### Boundaries & Safety

- Single writer lease: only one authoritative writer exists at a time, preventing two lives from overwriting each other.
- Shadow mode: read-only observation by default (`OBSERVE_ONLY`); no unsolicited external side effects.
- Cutover requires a signed handoff and complete evidence that the old writer stopped; rollback verifies the event list before proceeding.
- Python/Shell run in sandboxes with private workspaces, sanitized environments, resource limits, and atomic write-back; A5-class sensitive operations are hard-refused by deterministic policy and cannot be bypassed by the model.

---

## Product Components

| Component | Description |
|---|---|
| Desktop app | Electron application: chat, knowledge base, skills, body/avatar, settings, and life panel |
| Single-process gateway | `tiangong-total-gateway.exe` on `127.0.0.1:7184`, embedding Runtime (Omni Body skill execution), LifeKernel, Communication, and Policy |
| Life authority source | `src/life_service/` (40+ modules); packaged runtime mirror at `app/life-service/runtime314/` |
| VRM virtual body | 3D avatar (AvatarSample_A and others), biomechanically calibrated natural stance, expressions, lip sync, gestures, and real-time driving |
| Bundled runtime | CPython 3.12 shipped with the package (`app/runtime/python312/`); no system Python required |
| Contracts | `tiangong.life.api.v2`, `tiangong.desktop.backend.v3`, `tiangong.communication.api.v1` |

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
src/total_gateway/            Single-process gateway (Runtime/Life/Communication embedded)
src/communication_service/    Communication modules (WeChat, Feishu, etc.)
app/life-service/runtime314/  Byte-identical packaged life runtime mirror
app/frontend-v2/              Frontend (life-panel, life-summary-block, VRM display, etc.)
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
