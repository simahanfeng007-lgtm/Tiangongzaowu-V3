# Capability Composition P0/P1 Baseline Lock

Status: implementation branch baseline record
Branch: `agent/capability-composition-p0-p1-v1`

## P0 exact baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Authoritative base branch: `main`
- Locked main HEAD: `ee6ebb118bd64f0aa9352555634759a8dda8b990`
- Design reference SHA: `437943f9bfde534584c344befd61c12fda46ce17`
- Delta: main is 18 commits ahead of the design reference.
- Rollback base: `ee6ebb118bd64f0aa9352555634759a8dda8b990`

The 18-commit delta materially changes P19 verification/completion authority. In particular the latest baseline modifies `src/total_gateway/completion_gate.py`, `orchestration.py`, `store.py`, `verification_plan_executor.py`, `verification_readiness.py`, and verification repair components. P1 therefore freezes composition contracts against the current authority model and does not reuse old P19 interface assumptions.

## Source authority lock

`source-ownership.json` schema is `tiangong.source-ownership.v2`.

Editable production roots remain:

- `src`
- `app/backend/tiangong-backend/v3`
- `app/backend/tiangong-backend/tiangong_kernel`

For this phase the only production source modified is under `src/contracts`.
Generated/runtime mirrors are not edited directly. They remain outputs of the existing source sync mechanism.

## Existing authorities preserved

P0/P1 does not create or replace any execution authority. The following remain authoritative:

- Total Gateway
- existing Runtime / BodyRuntime
- existing WorldState authority
- existing Memory SSoT
- existing Action Manifest / Action Registry / ActionPermission
- existing Policy / Ticket / CapabilityGrant chain
- existing P19 verification plane
- existing CompletionGate
- existing source authority / generated mirror sync

## P1 frozen invariants

- I01 one Gateway
- I02 one Runtime
- I03 one WorldState
- I04 one Memory SSoT
- I05 one CompletionGate
- I06 one Action Registry authority
- I07 model cannot mint authority
- I08 World Context is non-authorizing
- I09 source revision is immutable per run
- I10 composition cannot expand permissions
- I11 P19 owns completion
- I12 Memory experience cannot become a WORLD fact by itself

## P1 contract surface

`src/contracts/capability_composition.py` introduces only side-effect-free contracts:

- `CapabilityDescriptorObservationV1`
- `ToolSourcePrimitiveV1`
- `SkillSourcePrimitiveV1`
- `SourceRevisionRefV1`
- `CompositionProposalV1`
- `CapabilityCompositionPlanV1`
- `CompositionValidationResultV1`
- `CapabilityCombinationExperienceV1`
- `AttributionIntegrityV1`
- `CompositionActivationContractV1`

The contract boundary explicitly separates:

1. model proposal data;
2. system-compiled plan identity;
3. conservative tri-state validation;
4. request/run/generation-scoped activation;
5. non-authorizing experience records.

No Planner, Compiler, Validator, Adapter, Tool Registry, Runtime, WorldState Store, or Memory Store is introduced in P1.

## Gate

P0 requires the current full regression gate to be green. This environment cannot execute the repository test suite locally and the GitHub combined-status endpoint did not expose usable checks for the base merge commit. Therefore the implementation branch must be validated by the repository's protected PR checks before P2 begins, including:

- `source-authority-ubuntu-latest`
- `source-authority-windows-latest`
- `full-regression-ubuntu-latest`
- `full-regression-windows-latest`

P2 MUST NOT begin on a red P0/P1 gate.
