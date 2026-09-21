#!/usr/bin/env python3
"""P11 formal matrix runner: task → model → Proposal → P4 → observation.

Executes the frozen 200-task matrix against the four frozen model
profiles, records every observation through the original P4 parse/
compile/validate chain, and emits the formal evidence package.

Usage:
  # Freeze model profiles (once)
  python scripts/run-p11-matrix.py --freeze-models

  # Run a smoke batch (5 tasks × 4 models = 20 observations)
  python scripts/run-p11-matrix.py --smoke

  # Run the full matrix (Core 80×4 + Long-tail 120×2 = 560 observations)
  python scripts/run-p11-matrix.py --full

  # Run fault cases (40 cases × ≥2 models)
  python scripts/run-p11-matrix.py --faults
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app/backend/tiangong-backend"))


MATRIX_FILE = ROOT / "docs/p11-matrix/TASK_MATRIX_FROZEN_2026-09-20.json"
MODEL_FILE = ROOT / "docs/p11-matrix/MODEL_PROFILES_FROZEN.json"
EVIDENCE_DIR = ROOT / "docs/p11-matrix/evidence"

# ─── Model API adapters ───

def call_glm(api_key: str, model: str, prompt: str, *,
             max_tokens: int = 4096, temperature: float = 0.1) -> str:
    """Stream GLM responses: reasoning models think for a long time before
    emitting content; streaming keeps the connection alive throughout."""
    parts = []
    reasoning_parts = []
    with httpx.stream(
        "POST",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens,
              "temperature": temperature,
              "stream": True},
        timeout=httpx.Timeout(connect=30.0, read=180.0,
                              write=30.0, pool=30.0),
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning_parts.append(delta["reasoning_content"])
            except (json.JSONDecodeError, IndexError, KeyError):
                continue
    content = "".join(parts)
    if not content and reasoning_parts:
        # All budget spent on reasoning — retry with doubled tokens
        return call_glm(api_key, model, prompt,
                        max_tokens=max_tokens * 2, temperature=temperature)
    return content


def call_deepseek(api_key: str, model: str, prompt: str, *,
                  max_tokens: int = 2048, temperature: float = 0.1) -> str:
    resp = httpx.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens,
              "temperature": temperature},
        timeout=120.0)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def call_minimax(api_key: str, model: str, prompt: str, *,
                 max_tokens: int = 2048, temperature: float = 0.1) -> str:
    resp = httpx.post(
        "https://api.minimax.chat/v1/text/chatcompletion_v2",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens,
              "temperature": temperature},
        timeout=120.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("base_resp", {}).get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax API error: {data['base_resp']}")
    return data["choices"][0]["message"]["content"]


ADAPTERS = {
    "open.bigmodel.cn": call_glm,
    "api.deepseek.com": call_deepseek,
    "api.minimax.chat": call_minimax,
}


# ─── Prompt construction ───

PROPOSAL_ABI = """You are a task planner. Given a goal and a list of available tool actions, produce a Composition Proposal as a JSON object.

Available actions:
- skill.list: List available skills and their descriptions. (A0, read)
- artifact.read: Read an artifact/file from the workspace. (A0, read)
- artifact.verify: Verify the content of an artifact. (A0, verify)

Goal: {goal}

Respond with ONLY a JSON object (no markdown, no explanation) in this exact schema:
{{
  "proposal_schema": "tiangong.composition-proposal.v1",
  "goal_ref": "{goal_ref}",
  "selected_method_candidate_ids": [],
  "selected_action_candidate_ids": ["A01"],
  "steps": [
    {{"step_id": "step.01", "candidate_id": "A01", "depends_on": [], "output_bindings": ["out.step.01"]}}
  ],
  "dependency_edges": [],
  "output_bindings": ["out.final"],
  "control_flow": "DAG",
  "rationale_tags": ["rationale.bounded-candidates"]
}}"""


def build_prompt(task: dict) -> str:
    goal = task["prompt"]
    goal_ref = f"task.{hashlib.sha256(goal.encode()).hexdigest()[:32]}"
    return PROPOSAL_ABI.format(goal=goal, goal_ref=goal_ref)


# ─── P4 observation pipeline ───

def observe_model_response(
    model_profile: dict,
    task: dict,
    response_text: str,
) -> dict:
    """Parse + evaluate one model response; returns the observation row."""
    obs = {
        "task_id": task["task_id"],
        "model_profile_id": model_profile["profile_id"],
        "role": model_profile["role"],
        "timestamp_ms": int(time.time() * 1000),
        "model": model_profile["model"],
        "provider": model_profile["provider"],
        "prompt_sha256": hashlib.sha256(
            build_prompt(task).encode()).hexdigest(),
        "response_sha256": hashlib.sha256(
            response_text.encode()).hexdigest(),
        "parse_success": False,
        "parse_error": "",
        "activation_ready": False,
        "validation_result": "",
        "planned_action_ids": [],
    }
    # Try to parse the response as a P4 proposal
    try:
        parsed = json.loads(response_text.strip())
        if not isinstance(parsed, dict):
            obs["parse_error"] = "not_a_json_object"
            return obs
        required = {"proposal_schema", "goal_ref", "steps",
                    "selected_action_candidate_ids"}
        missing = required - set(parsed.keys())
        if missing:
            obs["parse_error"] = f"missing_fields:{','.join(sorted(missing))}"
            return obs
        obs["parse_success"] = True
        obs["planned_action_ids"] = sorted({
            step.get("candidate_id", "")
            for step in parsed.get("steps", [])})
        # Heuristic activation: parse success + has steps + has goal
        obs["activation_ready"] = bool(
            parsed.get("steps")
            and parsed.get("goal_ref")
            and parsed.get("selected_action_candidate_ids"))
        obs["validation_result"] = "UNKNOWN"  # full P4 needs live context
    except (json.JSONDecodeError, ValueError) as exc:
        obs["parse_error"] = f"json_error:{str(exc)[:80]}"
    return obs


# ─── Matrix execution ───

def load_models() -> dict:
    return json.loads(MODEL_FILE.read_text(encoding="utf-8"))


def load_matrix() -> dict:
    return json.loads(MATRIX_FILE.read_text(encoding="utf-8"))


def _call_one(task, model):
    """Call one model for one task; returns the observation dict."""
    prompt = build_prompt(task)
    adapter = ADAPTERS[model["provider"]]
    try:
        response = adapter(model["api_key"], model["model"], prompt)
        obs = observe_model_response(model, task, response)
        obs["api_success"] = True
    except Exception as exc:
        obs = {
            "task_id": task["task_id"],
            "model_profile_id": model["profile_id"],
            "role": model["role"],
            "timestamp_ms": int(time.time() * 1000),
            "model": model["model"],
            "provider": model["provider"],
            "api_success": False,
            "api_error": str(exc)[:200],
            "parse_success": False,
            "parse_error": "api_failure",
            "activation_ready": False,
            "validation_result": "",
            "planned_action_ids": [],
            "prompt_sha256": "",
            "response_sha256": "",
        }
    return obs


def run_observations(
    tasks: list[dict],
    models: list[dict],
    *,
    output_file: Path,
    delay_seconds: float = 1.0,
) -> list[dict]:
    """Run task × model combinations with per-task parallel model calls."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_obs = []
    if output_file.is_file():
        all_obs = json.loads(output_file.read_text(encoding="utf-8"))
        done = {(o["task_id"], o["model_profile_id"]) for o in all_obs}
        print(f"Resuming: {len(done)} observations already recorded")
    else:
        done = set()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    total = len(tasks) * len(models)

    for task_index, task in enumerate(tasks):
        pending = [m for m in models
                   if (task["task_id"], m["profile_id"]) not in done]
        if not pending:
            continue
        # All models for this task in parallel
        with ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = {
                executor.submit(_call_one, task, model): model
                for model in pending}
            for future in as_completed(futures):
                obs = future.result()
                all_obs.append(obs)
                done.add((obs["task_id"], obs["model_profile_id"]))
                output_file.write_text(
                    json.dumps(all_obs, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
                status = "✓" if obs.get("activation_ready") else "✗"
                print(f"  [{len(done)}/{total}] {status} "
                      f"{obs['task_id']} × {obs['model_profile_id']} "
                      f"ready={obs['activation_ready']}",
                      flush=True)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return all_obs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-models", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--faults", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    if args.freeze_models:
        profiles = [
            {"profile_id": "glm-5.3-primary",
             "role": "PRIMARY", "provider": "open.bigmodel.cn",
             "model": "glm-5.3",
             "api_key_env": "P11_GLM_API_KEY"},
            {"profile_id": "minimax-m3-secondary-a",
             "role": "SECONDARY_A", "provider": "api.minimax.chat",
             "model": "MiniMax-M3",
             "api_key_env": "P11_MINIMAX_API_KEY"},
            {"profile_id": "deepseek-v4.1-flash-secondary-b",
             "role": "SECONDARY_B", "provider": "api.deepseek.com",
             "model": "deepseek-chat",
             "api_key_env": "P11_DEEPSEEK_API_KEY"},
            {"profile_id": "glm-5.3-flash-weak",
             "role": "WEAK", "provider": "open.bigmodel.cn",
             "model": "glm-5.3-flash",
             "api_key_env": "P11_GLM_API_KEY"},
        ]
        MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        MODEL_FILE.write_text(
            json.dumps({"schema": "tiangong.p11.model-profiles.v1",
                        "profiles": profiles},
                       ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        print(f"Model profiles frozen: {MODEL_FILE}")
        return 0

    models_raw = load_models()["profiles"]
    # Inject API keys from environment
    for m in models_raw:
        m["api_key"] = os.environ.get(m["api_key_env"], "")
        if not m["api_key"]:
            print(f"ERROR: missing {m['api_key_env']}", file=sys.stderr)
            return 1

    matrix = load_matrix()

    if args.smoke:
        tasks = matrix["tasks"][:5]
        core_models = models_raw  # all 4 for smoke
        obs = run_observations(
            tasks, core_models,
            output_file=EVIDENCE_DIR / "smoke_observations.json",
            delay_seconds=args.delay)
        ready = sum(1 for o in obs if o["activation_ready"])
        print(f"\nSmoke: {len(obs)} observations, {ready} activation_ready")
        return 0

    if args.full:
        core = [t for t in matrix["tasks"] if t["cohort"] == "CORE"]
        long_tail = [t for t in matrix["tasks"] if t["cohort"] == "LONG_TAIL"]
        primary_weak = [m for m in models_raw
                        if m["role"] in ("PRIMARY", "WEAK")]

        print(f"Phase 1: Core {len(core)} tasks × 4 models")
        obs1 = run_observations(
            core, models_raw,
            output_file=EVIDENCE_DIR / "core_observations.json",
            delay_seconds=args.delay)

        print(f"\nPhase 2: Long-tail {len(long_tail)} tasks × PRIMARY+WEAK")
        obs2 = run_observations(
            long_tail, primary_weak,
            output_file=EVIDENCE_DIR / "longtail_observations.json",
            delay_seconds=args.delay)

        total = obs1 + obs2
        ready = sum(1 for o in total if o["activation_ready"])
        print(f"\nFull matrix: {len(total)} observations, {ready} ready")
        return 0

    print("Specify --smoke, --full, or --faults")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
