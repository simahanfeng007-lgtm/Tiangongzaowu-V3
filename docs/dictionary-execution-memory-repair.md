# Dictionary execution and memory corrections

The correction loop previously could patch a program successfully and then
reuse the old exit-zero receipt for the same command, leaving its original
output wrong. Task actions now reach the existing Gateway replay decision.
Only static dictionary discovery remains eligible for the scheduler's simple
name/arguments cache.

Gateway binds mutable observations and local commands to recorded workspace
mutations and explicit local file versions. Successful effects retain their
post-state, so creating an output does not itself trigger another execution.
File writes and literal patches depend on their target; unrelated writes do
not force an already applied patch to run again. Append/send/create effects
retain their original identity. In-flight or ambiguous effects still require
resolution even when input versions change. Both serial and parallel scheduler
paths use this boundary. Verified replay receipts also count towards the new
composition's predecessor and learning evidence.

Local fingerprints cover targets, path/cwd arguments and argv file references.
They are bounded to the workspace, 128 references and 16 MiB of hashing per
snapshot (8 MiB per file); larger files use native metadata. Recorded mutations
invalidate implicit process dependencies. This does not claim complete tracking
of imports, environment changes or external edits to undeclared inputs. Remote
read observations are refreshed instead of treated as missing local files.

Composition memory changes:

- Attribute failed/ambiguous receipts to the referenced program before applying
  the request's terminal state. An unrelated task failure is unattributed, and
  cancellation cannot erase an observed failure. Only successful execution in
  a completed task increments successful reuse. Legacy attribution is recomputed
  from the same ledger when recalled; versioned events preserve the old evidence.
- Record feedback receipt order in the existing execution ledger before model
  interpretation. A newer withdrawal supersedes a slow earlier acceptance,
  including retries. A genuinely new acceptance remains possible.
- Read scoped current L3 heads with keyset pagination; historical versions no
  longer consume a fixed 4096-row window before filtering.
- An action may declare `repair_of` with the actual failed event hash returned
  in `execution_evidence`. Gateway verifies the same request, run, generation
  and action before admitting it. The target may change. A committed correction
  records recovery evidence, not user approval or proof of the cause. Unrelated
  success and ambiguous effects cannot establish this relation.

No fixed business Skills, new executor or separate memory store are introduced.
The full-dictionary/short-ID experiment remains outside production. Verification
Plane 1.35 includes these changes and the completion-gate simplification in the
explicitly refreshed authority freeze.

Regression entry points are `test_execution_replay_versions.py`,
`test_composition_memory_corrections.py`, and the inherited composition,
regenerative crash/recovery, memory store and authority-freeze suites. The scale
case uses 4096 valid migrated historical records plus current experience heads;
it is a storage-query test, not 4096 model-driven learning episodes. The existing
4096-root lineage contract remains a separate long-term accumulation boundary.
