# Dictionary-driven task composition

The dictionary defines atomic capabilities, argument/result contracts, execution
bindings, dependencies and budgets. **It contains no fixed business Skill
procedures.** The LLM interprets the user request and generates task-local Tools
(ordered action compositions) and a Skill (a graph of those generated Tools).
Neither a generated name nor a model claim creates a new permission.

## Normal execution

The ordinary frontend message enters the existing model/observation loop. The
native `omni_body` protocol accepts one `composition` containing `tools` and a
`skill`. Only capability discovery (`system.capabilities`, `system.action_schema`,
`system.health`) can be called directly. Task actions must belong to a registered
composition; the runtime does not wrap legacy direct calls and call that generation.

1. The model creates Tool definitions and a Skill from the current request and
   actual observations. Unknown file content requires an observation composition
   first, followed by a new model decision.
2. `capability_dictionary.composition` validates the complete declaration, action
   availability, unique identities and dependencies and compiles ordered leaves.
   The Gateway checks argument contracts and pins the actual dictionary digest.
3. `RegenerativeExecutionAuthority` records the exact program in the existing
   Gateway execution ledger under `composition.registered`, bound to the active
   Request/Run/Generation and original goal. It creates no Grant or permission.
4. Each leaf is checked against that registration before effect preparation.
   Changing its arguments, skipping an unfinished predecessor, using another
   request's registration or directly executing an uncomposed task action fails.
   Existing Policy, one-time signed Grants, workspace containment, deadlines,
   effect deduplication and verification remain in force.
5. A failed/blocked leaf stops the remaining composition. Actual results are
   aggregated into the original provider call's result; provider call IDs are
   never fabricated for internal leaves. The model can then generate a corrected
   composition. Each successful action retains its own factual evidence.
6. Completion still requires the original task's evidence. Registration or one
   successful composition does not mark the user task complete.

Compositions can include several generated Tools and actions; generation is not
one model call per primitive. The first implementation materializes graph leaves
serially to preserve dependencies and avoid speculative writes. Arguments are
concrete JSON values, not interpolated expressions. A change needing new facts
occurs in another model turn. This supports observation-driven work without
requiring a complete, predetermined task DAG.

The pre-existing P4/P7 whole-plan planner remains the explicit static strategy.
Incremental task programs use the existing P18 execution ledger/effect authority;
they do not pretend to be P7 whole-task admission receipts. The legacy planner
mode flag controls that static path, not normal task-composition generation.

The empty `skills/catalog.json` is retained only for historical release contract
compatibility. Runtime loading rejects a nonempty fixed-Skill catalog. Its 34
former business procedure files and reference document are removed. Generic
method-source primitives under `skills/methods` remain source semantics for the
static compiler; they are not executable business Skills. No `skill.get` content
is injected into normal prompts, even from a historical context envelope.

## Publication and continuation

Edit `tools/catalog.json`, `tools/schemas.json`, `tools/apps.json`,
`host-protocol.json` or `execution-profiles.json`, then run:

- `python scripts/build-dictionary.py`
- `python scripts/sync-generated-sources.py --write`
- `python scripts/build-dictionary.py --check`
- `python scripts/sync-generated-sources.py --check`

Restart after publication. The release marker binds all generated views; the
application release binds executor code. Source startup keeps installation,
profile, temporary data and workspace on the selected drive.

Task records and continuation projections preserve the original goal, dictionary
version, generated composition receipts and per-leaf observations. Programs remain
in the Gateway ledger across restart. A changed dictionary requires explicit
migration; an ambiguous effect requires reconciliation before repeat execution.
The model regenerates remaining work from the checkpoint and actual artifacts.

Native call/result groups are retained together across model turns and compacted
as complete groups. Each model call has a shared deadline for transport retries,
format correction and cancellation. Text/code, Office, image and media budgets
separate process logs from artifact sizes. Runtime budgets never grant new access.

## Verification status and limits

User-endorsed dynamic compositions can now be stored as reference DATA in the
existing memory authority and adapted in later tasks. This does not add fixed
business Skills. See [composition experience memory](composition-experience-memory.md)
for feedback, version binding, reuse evidence and current limits.

`test_task_generated_composition.py` exercises actual dictionary compilation,
durable program registration, real file mutation/read/hash, altered arguments,
missing dependencies, failed leaves, duplicate execution and fixed-Skill rejection.
`test_dictionary_runtime.py` covers release consistency, transport history,
continuation context and native bindings. Verification Plane 1.32 adds the program
compiler and Gateway ledger/effect binding to the frozen authority surface.

Earlier live artifact tests that selected or loaded fixed Skills do **not** prove
this corrected architecture works. New live acceptance must show an ordinary user
message leading to model-authored Tools and Skill, `composition.registered` in the
Gateway ledger, bound leaf effects and independently verified output. Seven work
categories, failure recovery and long-task restart remain separate acceptance
items; neither action counts nor a COMPLETED label substitutes for those tests.

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
