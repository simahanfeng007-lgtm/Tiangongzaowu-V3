"""Host-observed validity of task observations; never a new execution authority.

Mutable observations and local commands depend on recorded workspace mutations
and explicit local file versions. External side-effect intents keep their stable
identity. Unknown remote observations are refreshed instead of called facts.
"""
from pathlib import Path
import hashlib
import os

from contracts import canonical_sha256


def definition(action, release):
    row = release.tools.get(action, {})
    seen = set()
    while row.get("binding", {}).get("kind") == "alias" and action not in seen:
        seen.add(action)
        action = row["binding"]["target"]
        row = release.tools.get(action, {})
    return action, row


def state_dependent(call, release):
    action, row = definition(call["action"], release)
    return (row.get("effect") in {"read", "verify"}
            and action not in {"system.capabilities", "system.action_schema"}) or action in {
                "python.run", "shell.run", "file.write", "code.write", "code.patch_replace"}


def local_versions(call, workspace):
    """Bounded evidence, not a scan of arbitrary machine files or code parsing."""
    if workspace is None:
        return {}
    root = Path(workspace).resolve()
    args = call.get("args", {})
    paths = [call.get("target", "")]
    paths.extend(args.get(key, "") for key in ("path", "file_path", "cwd"))
    paths.extend(value for value in args.get("argv", []) if isinstance(value, str))
    versions = {}
    hash_budget = 16 * 1024 * 1024
    for value in paths[:128]:
        if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
            continue
        try:
            original = Path(value)
            path = (original if original.is_absolute() else root / original).resolve()
            if not path.is_relative_to(root):
                continue
            key = os.path.normcase(str(path))
            if not path.exists():
                versions[key] = {"exists": False}
            elif path.is_file():
                before = path.stat()
                # Hash normal scripts/inputs; large files use native revisions.
                row = {"size": before.st_size, "mtime_ns": str(before.st_mtime_ns),
                       "ctime_ns": str(before.st_ctime_ns)}
                if before.st_size <= min(8 * 1024 * 1024, hash_budget):
                    row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    hash_budget -= before.st_size
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    row["unstable"] = str(after.st_mtime_ns)
                versions[key] = row
            elif path.is_dir():
                versions[key] = {"directory_mtime_ns": str(path.stat().st_mtime_ns)}
        except (OSError, ValueError):
            continue
    return versions


def replay_basis(call, *, base_id, events, workspace, release, global_step):
    prepared = {e.effect_id: e for e in events if e.event_type == "step.prepared"}
    revision = None
    for event in events:
        if event.event_type not in {"step.dispatched", "step.committed", "step.failed", "step.ambiguous"}:
            continue
        prior = prepared.get(event.effect_id)
        if prior is None:
            continue
        if prior.payload.get("base_logical_effect_id", prior.logical_effect_id) == base_id:
            continue  # compare the command's verified post-state on repeat
        action = str(prior.payload.get("effect_namespace", "")).removeprefix("omni_body:")
        _, row = definition(action, release)
        if row.get("effect") not in {"read", "verify"}:
            revision = event.event_hash  # failures may also have partially written
    action, row = definition(call["action"], release)
    files = local_versions(call, workspace) if action.startswith(("file.", "code.")) or action in {"python.run", "shell.run"} else {}
    if action in {"file.write", "code.write", "code.patch_replace"}:
        # These operations depend on their target, not an unrelated workspace
        # write. Re-applying an already applied literal patch can itself fail.
        revision = None
    return {"mutation_revision": revision, "local_versions": files,
            "fresh_observation": global_step if not files and row.get("effect") in {"read", "verify"} else None}


def bind_state(payload, call, *, identity, events, workspace, release):
    """Return a descriptor chosen by Gateway, never an LLM-supplied version."""
    from .regenerative_execution import derive_logical_effect_id
    if call is None or not state_dependent(call, release):
        return dict(payload)
    base_id = payload["logical_effect_id"]
    basis = replay_basis(call, base_id=base_id, events=events, workspace=workspace,
                         release=release, global_step=payload["global_step"])
    prior = [e for e in events if e.event_type == "step.prepared"
             and e.payload.get("base_logical_effect_id", e.logical_effect_id) == base_id]
    terminals = {e.effect_id: e for e in events
                 if e.event_type in {"step.committed", "step.failed", "step.ambiguous", "step.reconciled"}}
    chosen = None
    for event in reversed(prior):
        terminal = terminals.get(event.effect_id)
        # Transport retry of one dispatch intent retains its identity even if
        # the filesystem changed after that dispatch.
        if event.payload.get("dispatch_step") == [payload["global_step"], payload.get("attempt", 1)]:
            chosen = event
            break
        if terminal is None or terminal.event_type == "step.ambiguous":
            chosen = event  # never hide an in-flight/unknown effect by re-versioning
            break
        if terminal.event_type == "step.reconciled" and terminal.payload.get("verdict") != "PROVEN_NOT_APPLIED":
            chosen = event
            break
        if terminal.event_type == "step.committed" and terminal.payload.get("replay_basis") == basis:
            chosen = event
            break
    postcondition = (chosen.payload["desired_postcondition_sha256"] if chosen else canonical_sha256({
        "domain": "tiangong.gateway.state-bound-tool-intent.v1",
        "intent": payload["desired_postcondition_sha256"], "basis": basis}))
    logical = derive_logical_effect_id(request_id=identity.request_id, run_id=identity.run_id,
        generation=identity.generation, obligation_key=payload["obligation_key"],
        effect_namespace=payload["effect_namespace"], normalized_target=payload["normalized_target"],
        desired_postcondition_sha256=postcondition)
    return {**payload, "logical_effect_id": logical, "desired_postcondition_sha256": postcondition,
            "base_logical_effect_id": base_id, "replay_basis": basis}
