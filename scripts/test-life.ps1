[CmdletBinding()]
param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = Join-Path $Root "src"

& (Join-Path $PSScriptRoot "sync-life-source.ps1")

if ($Full) {
  python -m unittest discover -s (Join-Path $Root "tests") -v
} else {
  $Tests = @(
    "tests.test_life_source_ownership",
    "tests.test_life_contracts_v3",
    "tests.test_causal_replay",
    "tests.test_viability_math",
    "tests.test_causal_autonomy_p7",
    "tests.test_reflection_capability_p8",
    "tests.test_skill_authority_p9",
    "tests.test_atomic_context_p10",
    "tests.test_life_frontend_p10",
    "tests.test_life_cutover_p11",
    "tests.test_affect_appraisal_v3",
    "tests.test_affect_external_intake",
    "tests.test_affect_expression_cases",
    "tests.test_continuity_capsule",
    "tests.test_causal_memory_contracts",
    "tests.test_causal_memory_store",
    "tests.test_causal_context_builder",
    "tests.test_legacy_memory_migration",
    "tests.test_object_gc_dry_run",
    "tests.test_agency_contracts_v3",
    "tests.test_policy_engine_p6",
    "tests.test_operational_key_lifecycle",
    "tests.test_omni_capability_guard",
    "tests.test_life_action_intent_boundary",
    "tests.test_life_shadow_store",
    "tests.test_life_event_ingress",
    "tests.test_gateway_life_continuity",
    "tests.test_life_shadow_compat",
    "tests.test_life_runtime_fixes",
    "tests.test_context_projection",
    "tests.test_completion_gate",
    "tests.test_delivery_outbox",
    "tests.test_autonomous_completion",
    "tests.test_frozen_backend_compat",
    "tests.test_life_client",
    "tests.test_release_manifest",
    "tests.test_desktop_completion_wiring"
  )
  python -m unittest $Tests -v
}
if ($LASTEXITCODE -ne 0) {
  throw "Life verification failed."
}

Write-Host "Life verification passed."
