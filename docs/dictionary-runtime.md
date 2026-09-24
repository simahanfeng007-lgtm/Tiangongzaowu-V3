# Tool / Skill dictionary runtime

`dictionaries/` is the editable capability source. The model-facing action list,
parameter contracts, Skill procedures, execution bindings, dependency readiness,
and generated Gateway views derive from this package. Retained `omni_body_skill`
Python modules implement adapters; their former registry and Skill directories
are retired and are not startup dependencies.

## Publish and run

1. Edit `tools/catalog.json`, `tools/schemas.json`, `tools/apps.json`,
   `skills/catalog.json`, the referenced procedure, `host-protocol.json`, or
   `execution-profiles.json`.
2. Run `python scripts/build-dictionary.py`. It validates identifiers, required
   references, aliases, dependency declarations, and actual execution bindings.
3. Run `python scripts/sync-generated-sources.py --write`, then `--check`.
4. Restart the source application. `scripts/start-source.ps1 -ProfileRoot D:\...`
   keeps the profile on the selected drive; its default is beside the checkout.

`build-dictionary.py --check` verifies published bytes without changing them.
The release marker is written last and binds generated views. The source release
also binds the actual executor source. A running process retains its loaded
dictionary and procedure bytes. An interrupted task whose saved dictionary hash
differs must be migrated explicitly; it must not silently replay on new bindings.

## Execution

Ordinary conversation and the former `[字典执行]` prefix use the same dynamic
dictionary loop. Tool results feed the next decision. Simple actions can execute
directly; complex tasks can load a matching Skill with `skill.get` / `skill.read`.
These commands now resolve only the dictionary through Gateway. A candidate is
not an activated Skill. Explicit frontend selection loads and pins the complete
procedure through the same authority before inference.

Raw user text, inherited goal, project hint, and selected Skill IDs travel in
`task_context`; a project hint is not filesystem authorization. Static DAG
planning remains an optional strategy (`execution_strategy: "static"`) under
the existing admission policy. It is not required to discover or use a Tool.

Readiness distinguishes a definition from an available implementation. Required
dependencies block an action; optional dependencies are reported without
blocking it. `system.action_schema` returns both the contract and readiness.
Counts of definitions, compiled bindings, and actually tested capabilities have
different meanings and must not be presented as interchangeable.

Application discovery metadata is migrated in `tools/apps.json`; an application
entry alone does not prove its external adapter is configured. The former
standalone installer, per-user implementation overrides, nested V3 registries,
and alternate model-tool schema files are retired. Only the installed adapter
package can implement a dictionary binding. The migration inventory records
stable IDs and distinguishes retained implementations from unavailable adapters.

## Budgets and completion

Each model call has one monotonic deadline shared by HTTP retries, format repair,
and cancellation. The normal ceiling remains 300 seconds and the parent's
remaining budget can shorten it. There is no independent 180-second join.
There are at most two transient retries and one truncated/invalid-output repair.
Tool argument fragments never execute. `[DONE]`, terminal reasons, usage tails,
HTTP-200 error events, malformed JSON, and unexpected EOF have explicit handling.
Closing the local response does not prove remote billing stopped.

Native call/result pairs remain associated with provider, protocol, model, and
call ID. Compaction removes whole pairs; full loaded Skill text is re-injected
independently of the bounded observation history. Recovery uses saved facts and
observations; uncertain effects must be inspected before retrying.

When complete native pairs are present, their duplicate textual observations
become compact host-check summaries. Warnings and unpaired observations remain;
cross-provider/model changes retain the textual fallback. Full facts stay in the
Gateway. Explicit continuation also restores the original goal and pinned Skill
IDs after a network/format failure, not only after a timeout. A newer completed
request in the same conversation supersedes an earlier failed task.

Text/code, Office, image, and media profiles separate process log limits from
artifact size limits. They narrow existing host resources and never grant new
filesystem, network, Python, or shell permissions. Static composition retains its
separately signed execution profile constraints.

Local handoff is validated using actual artifact evidence and final inspection.
External upload/send requests still need delivery evidence. Model errors and
incomplete responses cannot terminate as a successful chat reply. Model claims
alone are not acceptance evidence.

Uncommitted model responses may be retried within the same parent deadline after
a broken stream. Discarded partial tool arguments cannot execute. JSON repair
instructions follow native history in the actual provider request, and attempt
metrics retain finish reasons and progress timings without private reasoning.

Current continuity, explicit constraints and required stored memories are kept
before optional history. History over budget is omitted with an explicit count;
it cannot block a new task as an identity failure. Required context that cannot
fit returns `life.context.budget_exceeded`. Recent memory selection uses timestamps
so saving or restarting cannot change its meaning through JSON key order.

## Verification

`tests/test_dictionary_model_lifecycle.py` injects stream termination, retry,
cancellation, delayed result, and shared-deadline faults. A virtual 215-second
valid generation checks removal of the former 180-second cutoff.
`tests/test_dictionary_runtime.py` checks publication drift, missing dependencies,
bad aliases/references, procedure preservation, historical envelope compatibility,
three native transport rounds, and a real dictionary-bound file mutation.

The live acceptance matrix uses the hidden source frontend form and the saved
provider configuration. It covers file/code tasks, novels, video, images,
spreadsheets, HTML, and PPT. Acceptance requires independent checks of generated
files; a terminal state or a model saying "completed" is insufficient. Live
results are recorded separately from deterministic test results.

Verification Plane 1.31 declares the changed authority surface: dictionary-backed
capability loading, native installed Method provenance, optional Skill grants,
raw task context, bounded history selection, and the canonical CLI host. Its freeze manifest is regenerated with the existing guard
after these changes; byte hashes do not substitute for the behavioral tests.
The former `run-all-skills-smoke.py` is retired because it used synthetic learning
receipts and compatibility execution, and cannot establish real acceptance.

## Windows runtime access

Source setup/startup and the explicit Windows CI bootstrap provision one
path-specific AppContainer capability with read/execute access to the interpreter.
Every task retains its own package SID, writable private workspace and Job Object,
and receives only that runtime capability, with no network capability. A receipt
records completed inheritance propagation; actual ACL readback is required before
reuse. Normal tasks never grant/revoke permissions across the shared runtime tree.
A newly registered executable can provision once within the existing execution
deadline; lock waits and ACL helpers honor cancellation. Failed preparation never
starts user code, and process cleanup cannot mask success with a runtime ACL revoke.

Native tests cover parallel first provisioning, stale receipts, repeated parallel
execution with zero shared ACL mutations, denied runtime writes, peer workspace
isolation, network denial and cancellation. This removes the observed 20/15-second
per-task `icacls` grant/revoke failures rather than extending those timeouts.
