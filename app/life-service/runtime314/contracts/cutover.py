"""Single-epoch channel drain and ownership transfer contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .models import ContractModel, OpaqueId, SCHEMA_BASE, LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Sha256


ChannelOperation = Literal["POLL", "SEND"]

# v2.1 promotion is deliberately a data contract, not a process or policy
# engine.  It only proves that a build passed the frozen gate DAG before a
# caller changes its routing mode.
V21Gate = Literal["BASELINE", "G0", "G1", "G2", "G3", "G4", "G5", "G6A", "G6B"]
V21Mode = Literal["legacy_observe", "shadow", "canary_internal", "canary_effect", "active"]
_V21_GATE_PREDECESSOR: dict[str, str] = {
    "G0": "BASELINE", "G1": "G0", "G2": "G1", "G3": "G2",
    "G4": "G3", "G5": "G4", "G6A": "G5", "G6B": "G6A",
}
_V21_MODE_INDEX = {name: index for index, name in enumerate((
    "legacy_observe", "shadow", "canary_internal", "canary_effect", "active",
))}


def derive_gate_promotion_id(
    to_gate: str, promotion_epoch: int, build_id: str, source_manifest_sha256: str,
) -> str:
    return "gpr_" + canonical_sha256({
        "domain": "tiangong.v21.gate-promotion.v1", "to_gate": to_gate,
        "promotion_epoch": promotion_epoch, "build_id": build_id,
        "source_manifest_sha256": source_manifest_sha256,
    })


class GatePromotionRecord(ContractModel):
    """Immutable CAS receipt for the frozen v2.1 gate DAG."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    promotion_id: str = Field(pattern=r"^gpr_[0-9a-f]{64}$")
    promotion_epoch: int = Field(ge=1)
    expected_current_promotion_sha256: Sha256
    from_gate: V21Gate
    to_gate: V21Gate
    from_mode: V21Mode
    to_mode: V21Mode
    build_id: str = Field(min_length=1, max_length=160)
    source_manifest_sha256: Sha256
    contract_set_hash: Sha256
    config_hash: Sha256
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=256)
    rollback_target: str = Field(min_length=1, max_length=160)
    promoted_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    promotion_sha256: Sha256

    @model_validator(mode="after")
    def validate_promotion(self) -> Self:
        if self.to_gate == "BASELINE":
            raise ValueError("baseline is not a promotable gate")
        if _V21_GATE_PREDECESSOR[self.to_gate] != self.from_gate:
            raise ValueError("gate promotion dependency is invalid")
        if self.to_gate == "G0":
            if self.expected_current_promotion_sha256 != "0" * 64:
                raise ValueError("G0 promotion must CAS from the zero head")
        elif self.expected_current_promotion_sha256 == "0" * 64:
            raise ValueError("non-G0 promotion requires a current promotion head")
        if _V21_MODE_INDEX[self.to_mode] < _V21_MODE_INDEX[self.from_mode] or (
            _V21_MODE_INDEX[self.to_mode] - _V21_MODE_INDEX[self.from_mode] > 1
        ):
            raise ValueError("promotion mode transition is invalid")
        if self.promotion_id != derive_gate_promotion_id(
            self.to_gate, self.promotion_epoch, self.build_id, self.source_manifest_sha256,
        ):
            raise ValueError("gate promotion identity is invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs) or any(not ref for ref in self.evidence_refs):
            raise ValueError("promotion evidence refs must be nonempty and unique")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"promotion_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.promotion_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"promotion_sha256": self.computed_sha256()})


def _strictly_valid(value: ContractModel) -> bool:
    try:
        validated = type(value).model_validate(value.model_dump(mode="python"), strict=True)
    except ValueError:
        return False
    return validated == value


def derive_cutover_id(
    channel: str,
    tenant_id: str,
    link_account_id: str,
    gateway_epoch: int,
) -> str:
    return "cut_" + canonical_sha256(
        {
            "domain": "tiangong.migration.channel-cutover.v1",
            "channel": channel,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "gateway_epoch": gateway_epoch,
        }
    )


class ChannelDrainEvidence(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ChannelDrainEvidence",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    evidence_id: str = Field(pattern=r"^drn_[0-9a-f]{64}$")
    cutover_id: str = Field(pattern=r"^cut_[0-9a-f]{64}$")
    migration_epoch: int = Field(ge=1)
    gateway_epoch: int = Field(ge=1)
    channel: Literal["wechat", "feishu"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    legacy_owner_component_id: OpaqueId
    legacy_owner_instance_id: OpaqueId
    poller_stopped: Literal[True]
    sender_stopped: Literal[True]
    inflight_poll_count: Literal[0]
    inflight_send_count: Literal[0]
    unacknowledged_inbox_count: Literal[0]
    unresolved_delivery_count: Literal[0]
    inbox_ledger_sha256: Sha256
    delivery_ledger_sha256: Sha256
    last_cursor_sha256: Sha256 | None = None
    observed_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.migration_epoch != self.gateway_epoch:
            raise ValueError("channel drain must use the current single gateway epoch")
        if self.cutover_id != derive_cutover_id(
            self.channel,
            self.tenant_id,
            self.link_account_id,
            self.gateway_epoch,
        ):
            raise ValueError("channel drain cutover identity is invalid")
        expected = "drn_" + canonical_sha256(
            {
                "domain": "tiangong.migration.channel-drain-evidence.v1",
                "cutover_id": self.cutover_id,
                "legacy_owner_component_id": self.legacy_owner_component_id,
                "legacy_owner_instance_id": self.legacy_owner_instance_id,
                "inbox_ledger_sha256": self.inbox_ledger_sha256,
                "delivery_ledger_sha256": self.delivery_ledger_sha256,
                "last_cursor_sha256": self.last_cursor_sha256,
            }
        )
        if self.evidence_id != expected:
            raise ValueError("channel drain evidence identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"evidence_sha256": self.computed_sha256()})


def build_channel_drain_evidence(
    *,
    channel: Literal["wechat", "feishu"],
    tenant_id: str,
    link_account_id: str,
    gateway_epoch: int,
    legacy_owner_component_id: str,
    legacy_owner_instance_id: str,
    inbox_ledger_sha256: str,
    delivery_ledger_sha256: str,
    last_cursor_sha256: str | None,
    observed_at_ms: int,
) -> ChannelDrainEvidence:
    cutover_id = derive_cutover_id(channel, tenant_id, link_account_id, gateway_epoch)
    evidence_id = "drn_" + canonical_sha256(
        {
            "domain": "tiangong.migration.channel-drain-evidence.v1",
            "cutover_id": cutover_id,
            "legacy_owner_component_id": legacy_owner_component_id,
            "legacy_owner_instance_id": legacy_owner_instance_id,
            "inbox_ledger_sha256": inbox_ledger_sha256,
            "delivery_ledger_sha256": delivery_ledger_sha256,
            "last_cursor_sha256": last_cursor_sha256,
        }
    )
    return ChannelDrainEvidence(
        evidence_id=evidence_id,
        cutover_id=cutover_id,
        migration_epoch=gateway_epoch,
        gateway_epoch=gateway_epoch,
        channel=channel,
        tenant_id=tenant_id,
        link_account_id=link_account_id,
        legacy_owner_component_id=legacy_owner_component_id,
        legacy_owner_instance_id=legacy_owner_instance_id,
        poller_stopped=True,
        sender_stopped=True,
        inflight_poll_count=0,
        inflight_send_count=0,
        unacknowledged_inbox_count=0,
        unresolved_delivery_count=0,
        inbox_ledger_sha256=inbox_ledger_sha256,
        delivery_ledger_sha256=delivery_ledger_sha256,
        last_cursor_sha256=last_cursor_sha256,
        observed_at_ms=observed_at_ms,
        evidence_sha256="0" * 64,
    ).with_computed_sha256()


class ChannelOwnershipLease(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ChannelOwnershipLease",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    lease_id: str = Field(pattern=r"^own_[0-9a-f]{64}$")
    cutover_id: str = Field(pattern=r"^cut_[0-9a-f]{64}$")
    migration_epoch: int = Field(ge=1)
    gateway_epoch: int = Field(ge=1)
    channel: Literal["wechat", "feishu"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    owner_component_id: Literal["tiangong-communication-service"]
    owner_instance_id: OpaqueId
    allowed_operations: tuple[ChannelOperation, ...]
    drain_evidence_id: str = Field(pattern=r"^drn_[0-9a-f]{64}$")
    drain_evidence_sha256: Sha256
    component_manifest_sha256: Sha256
    previous_lease_sha256: Sha256 | None = None
    issued_at_ms: int = Field(ge=0)
    not_before_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    lease_sha256: Sha256

    @model_validator(mode="after")
    def validate_lease(self) -> Self:
        if self.migration_epoch != self.gateway_epoch:
            raise ValueError("channel ownership must use the current single gateway epoch")
        if self.cutover_id != derive_cutover_id(
            self.channel,
            self.tenant_id,
            self.link_account_id,
            self.gateway_epoch,
        ):
            raise ValueError("channel ownership cutover identity is invalid")
        if self.allowed_operations != ("POLL", "SEND"):
            raise ValueError("channel ownership lease must grant exactly poll and send")
        if not (
            self.issued_at_ms == self.not_before_ms
            and self.not_before_ms < self.expires_at_ms
            and self.expires_at_ms - self.not_before_ms <= 60_000
        ):
            raise ValueError("channel ownership lease lifetime is invalid")
        expected = "own_" + canonical_sha256(
            {
                "domain": "tiangong.migration.channel-ownership-lease.v1",
                "cutover_id": self.cutover_id,
                "migration_epoch": self.migration_epoch,
                "owner_component_id": self.owner_component_id,
                "owner_instance_id": self.owner_instance_id,
                "drain_evidence_sha256": self.drain_evidence_sha256,
                "component_manifest_sha256": self.component_manifest_sha256,
                "previous_lease_sha256": self.previous_lease_sha256,
                "issued_at_ms": self.issued_at_ms,
                "expires_at_ms": self.expires_at_ms,
            }
        )
        if self.lease_id != expected:
            raise ValueError("channel ownership lease identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"lease_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.lease_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"lease_sha256": self.computed_sha256()})


class ChannelCutoverSnapshot(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ChannelCutoverSnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    cutover_id: str = Field(pattern=r"^cut_[0-9a-f]{64}$")
    migration_epoch: int = Field(ge=1)
    gateway_epoch: int = Field(ge=1)
    channel: Literal["wechat", "feishu"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    state: Literal["DRAINING", "DRAINED", "CANDIDATE_ACTIVE"]
    legacy_owner_component_id: OpaqueId
    legacy_owner_instance_id: OpaqueId
    candidate_owner_instance_id: OpaqueId
    drain_evidence_id: str | None = Field(default=None, pattern=r"^drn_[0-9a-f]{64}$")
    drain_evidence_sha256: Sha256 | None = None
    active_lease_id: str | None = Field(default=None, pattern=r"^own_[0-9a-f]{64}$")
    active_lease_sha256: Sha256 | None = None
    revision: int = Field(ge=1, le=3)
    started_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.migration_epoch != self.gateway_epoch:
            raise ValueError("channel cutover must use the current single gateway epoch")
        if self.cutover_id != derive_cutover_id(
            self.channel,
            self.tenant_id,
            self.link_account_id,
            self.gateway_epoch,
        ):
            raise ValueError("channel cutover identity is invalid")
        if self.updated_at_ms < self.started_at_ms:
            raise ValueError("channel cutover time is invalid")
        expected_revision = {"DRAINING": 1, "DRAINED": 2, "CANDIDATE_ACTIVE": 3}[self.state]
        if self.revision != expected_revision:
            raise ValueError("channel cutover revision is invalid")
        has_drain = self.drain_evidence_id is not None and self.drain_evidence_sha256 is not None
        has_lease = self.active_lease_id is not None and self.active_lease_sha256 is not None
        if (self.drain_evidence_id is None) != (self.drain_evidence_sha256 is None):
            raise ValueError("channel cutover drain binding is partial")
        if (self.active_lease_id is None) != (self.active_lease_sha256 is None):
            raise ValueError("channel cutover lease binding is partial")
        if self.state == "DRAINING" and (has_drain or has_lease):
            raise ValueError("draining cutover cannot already contain evidence or lease")
        if self.state == "DRAINED" and (not has_drain or has_lease):
            raise ValueError("drained cutover binding is invalid")
        if self.state == "CANDIDATE_ACTIVE" and (not has_drain or not has_lease):
            raise ValueError("active cutover requires drain evidence and one lease")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"snapshot_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.snapshot_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"snapshot_sha256": self.computed_sha256()})


def begin_channel_cutover(
    *,
    channel: Literal["wechat", "feishu"],
    tenant_id: str,
    link_account_id: str,
    gateway_epoch: int,
    legacy_owner_component_id: str,
    legacy_owner_instance_id: str,
    candidate_owner_instance_id: str,
    started_at_ms: int,
) -> ChannelCutoverSnapshot:
    return ChannelCutoverSnapshot(
        cutover_id=derive_cutover_id(channel, tenant_id, link_account_id, gateway_epoch),
        migration_epoch=gateway_epoch,
        gateway_epoch=gateway_epoch,
        channel=channel,
        tenant_id=tenant_id,
        link_account_id=link_account_id,
        state="DRAINING",
        legacy_owner_component_id=legacy_owner_component_id,
        legacy_owner_instance_id=legacy_owner_instance_id,
        candidate_owner_instance_id=candidate_owner_instance_id,
        revision=1,
        started_at_ms=started_at_ms,
        updated_at_ms=started_at_ms,
        snapshot_sha256="0" * 64,
    ).with_computed_sha256()


def apply_channel_drain(
    snapshot: ChannelCutoverSnapshot,
    evidence: ChannelDrainEvidence,
) -> ChannelCutoverSnapshot:
    if (
        snapshot.state != "DRAINING"
        or not _strictly_valid(snapshot)
        or not _strictly_valid(evidence)
        or not snapshot.has_valid_sha256()
        or not evidence.has_valid_sha256()
        or evidence.cutover_id != snapshot.cutover_id
        or evidence.gateway_epoch != snapshot.gateway_epoch
        or evidence.legacy_owner_component_id != snapshot.legacy_owner_component_id
        or evidence.legacy_owner_instance_id != snapshot.legacy_owner_instance_id
        or evidence.observed_at_ms < snapshot.started_at_ms
        or not evidence.poller_stopped
        or not evidence.sender_stopped
        or evidence.inflight_poll_count != 0
        or evidence.inflight_send_count != 0
        or evidence.unacknowledged_inbox_count != 0
        or evidence.unresolved_delivery_count != 0
        or evidence.model_generated
    ):
        raise ValueError("channel drain evidence is not bound to the draining cutover")
    return snapshot.model_copy(
        update={
            "state": "DRAINED",
            "drain_evidence_id": evidence.evidence_id,
            "drain_evidence_sha256": evidence.evidence_sha256,
            "revision": 2,
            "updated_at_ms": evidence.observed_at_ms,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()


def activate_candidate_owner(
    snapshot: ChannelCutoverSnapshot,
    evidence: ChannelDrainEvidence,
    *,
    component_manifest_sha256: str,
    issued_at_ms: int,
    lease_ttl_ms: int = 30_000,
) -> tuple[ChannelCutoverSnapshot, ChannelOwnershipLease]:
    if (
        snapshot.state != "DRAINED"
        or not _strictly_valid(snapshot)
        or not _strictly_valid(evidence)
        or not snapshot.has_valid_sha256()
        or not evidence.has_valid_sha256()
        or snapshot.drain_evidence_id != evidence.evidence_id
        or snapshot.drain_evidence_sha256 != evidence.evidence_sha256
        or issued_at_ms < snapshot.updated_at_ms
        or not 1_000 <= lease_ttl_ms <= 60_000
    ):
        raise ValueError("candidate owner cannot activate before an exact completed drain")
    lease = _build_ownership_lease(
        snapshot,
        evidence,
        component_manifest_sha256=component_manifest_sha256,
        previous_lease_sha256=None,
        issued_at_ms=issued_at_ms,
        lease_ttl_ms=lease_ttl_ms,
    )
    active = snapshot.model_copy(
        update={
            "state": "CANDIDATE_ACTIVE",
            "active_lease_id": lease.lease_id,
            "active_lease_sha256": lease.lease_sha256,
            "revision": 3,
            "updated_at_ms": issued_at_ms,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()
    return active, lease


def _build_ownership_lease(
    snapshot: ChannelCutoverSnapshot,
    evidence: ChannelDrainEvidence,
    *,
    component_manifest_sha256: str,
    previous_lease_sha256: str | None,
    issued_at_ms: int,
    lease_ttl_ms: int,
) -> ChannelOwnershipLease:
    expires_at_ms = issued_at_ms + lease_ttl_ms
    identity = {
        "domain": "tiangong.migration.channel-ownership-lease.v1",
        "cutover_id": snapshot.cutover_id,
        "migration_epoch": snapshot.migration_epoch,
        "owner_component_id": "tiangong-communication-service",
        "owner_instance_id": snapshot.candidate_owner_instance_id,
        "drain_evidence_sha256": evidence.evidence_sha256,
        "component_manifest_sha256": component_manifest_sha256,
        "previous_lease_sha256": previous_lease_sha256,
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
    }
    return ChannelOwnershipLease(
        lease_id="own_" + canonical_sha256(identity),
        cutover_id=snapshot.cutover_id,
        migration_epoch=snapshot.migration_epoch,
        gateway_epoch=snapshot.gateway_epoch,
        channel=snapshot.channel,
        tenant_id=snapshot.tenant_id,
        link_account_id=snapshot.link_account_id,
        owner_component_id="tiangong-communication-service",
        owner_instance_id=snapshot.candidate_owner_instance_id,
        allowed_operations=("POLL", "SEND"),
        drain_evidence_id=evidence.evidence_id,
        drain_evidence_sha256=evidence.evidence_sha256,
        component_manifest_sha256=component_manifest_sha256,
        previous_lease_sha256=previous_lease_sha256,
        issued_at_ms=issued_at_ms,
        not_before_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        lease_sha256="0" * 64,
    ).with_computed_sha256()


def renew_candidate_owner(
    snapshot: ChannelCutoverSnapshot,
    evidence: ChannelDrainEvidence,
    current_lease: ChannelOwnershipLease,
    *,
    issued_at_ms: int,
    lease_ttl_ms: int = 30_000,
) -> tuple[ChannelCutoverSnapshot, ChannelOwnershipLease]:
    if (
        snapshot.state != "CANDIDATE_ACTIVE"
        or not _strictly_valid(snapshot)
        or not _strictly_valid(evidence)
        or not _strictly_valid(current_lease)
        or not snapshot.has_valid_sha256()
        or not evidence.has_valid_sha256()
        or not current_lease.has_valid_sha256()
        or snapshot.drain_evidence_id != evidence.evidence_id
        or snapshot.drain_evidence_sha256 != evidence.evidence_sha256
        or snapshot.active_lease_id != current_lease.lease_id
        or snapshot.active_lease_sha256 != current_lease.lease_sha256
        or current_lease.cutover_id != snapshot.cutover_id
        or current_lease.gateway_epoch != snapshot.gateway_epoch
        or current_lease.owner_instance_id != snapshot.candidate_owner_instance_id
        or not current_lease.issued_at_ms < issued_at_ms <= current_lease.expires_at_ms
        or not 1_000 <= lease_ttl_ms <= 60_000
    ):
        raise ValueError("candidate ownership renewal does not continue the active lease")
    lease = _build_ownership_lease(
        snapshot,
        evidence,
        component_manifest_sha256=current_lease.component_manifest_sha256,
        previous_lease_sha256=current_lease.lease_sha256,
        issued_at_ms=issued_at_ms,
        lease_ttl_ms=lease_ttl_ms,
    )
    renewed = snapshot.model_copy(
        update={
            "active_lease_id": lease.lease_id,
            "active_lease_sha256": lease.lease_sha256,
            "updated_at_ms": issued_at_ms,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()
    return renewed, lease


EXECUTION_CONTRACT_FROM_SCHEMA_VERSION = "tiangong.gateway.contracts.v1"
EXECUTION_CONTRACT_TO_SCHEMA_VERSION = "tiangong.gateway.contracts.v2"

ExecutionContractCutoverState = Literal[
    "FENCING",
    "DRAINED",
    "HEAD_PINNED",
    "TERMINAL_FENCED",
    "ACTIVE",
]

_EXECUTION_CONTRACT_CUTOVER_REVISION = {
    "FENCING": 1,
    "DRAINED": 2,
    "HEAD_PINNED": 3,
    "TERMINAL_FENCED": 4,
    "ACTIVE": 5,
}


def derive_execution_contract_cutover_id(
    from_schema_version: str,
    to_schema_version: str,
    gateway_epoch: int,
) -> str:
    return "cut_" + canonical_sha256(
        {
            "domain": "tiangong.migration.execution-contract-cutover.v1",
            "from_schema_version": from_schema_version,
            "to_schema_version": to_schema_version,
            "gateway_epoch": gateway_epoch,
        }
    )


def _derive_execution_contract_drain_evidence_id(
    cutover_id: str,
    effect_ledger_sha256: str,
    state_ledger_sha256: str,
    ticket_ledger_sha256: str,
) -> str:
    return "ecx_" + canonical_sha256(
        {
            "domain": "tiangong.migration.execution-contract-drain.v1",
            "cutover_id": cutover_id,
            "effect_ledger_sha256": effect_ledger_sha256,
            "state_ledger_sha256": state_ledger_sha256,
            "ticket_ledger_sha256": ticket_ledger_sha256,
        }
    )


class ExecutionContractDrainEvidence(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ExecutionContractDrainEvidence",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    evidence_id: str = Field(pattern=r"^ecx_[0-9a-f]{64}$")
    cutover_id: str = Field(pattern=r"^cut_[0-9a-f]{64}$")
    migration_epoch: int = Field(ge=1)
    gateway_epoch: int = Field(ge=1)
    from_schema_version: Literal["tiangong.gateway.contracts.v1"]
    to_schema_version: Literal["tiangong.gateway.contracts.v2"]
    inflight_execution_count: Literal[0]
    unclaimed_ticket_count: Literal[0]
    unresolved_ambiguous_count: Literal[0]
    open_vold_intent_count: Literal[0]
    effect_ledger_sha256: Sha256
    state_ledger_sha256: Sha256
    ticket_ledger_sha256: Sha256
    observed_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.migration_epoch != self.gateway_epoch:
            raise ValueError("execution contract drain must use the current single gateway epoch")
        if self.cutover_id != derive_execution_contract_cutover_id(
            self.from_schema_version,
            self.to_schema_version,
            self.gateway_epoch,
        ):
            raise ValueError("execution contract drain cutover identity is invalid")
        if self.evidence_id != _derive_execution_contract_drain_evidence_id(
            self.cutover_id,
            self.effect_ledger_sha256,
            self.state_ledger_sha256,
            self.ticket_ledger_sha256,
        ):
            raise ValueError("execution contract drain evidence identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"evidence_sha256": self.computed_sha256()})


def build_execution_contract_drain_evidence(
    *,
    gateway_epoch: int,
    effect_ledger_sha256: str,
    state_ledger_sha256: str,
    ticket_ledger_sha256: str,
    observed_at_ms: int,
) -> ExecutionContractDrainEvidence:
    cutover_id = derive_execution_contract_cutover_id(
        EXECUTION_CONTRACT_FROM_SCHEMA_VERSION,
        EXECUTION_CONTRACT_TO_SCHEMA_VERSION,
        gateway_epoch,
    )
    return ExecutionContractDrainEvidence(
        evidence_id=_derive_execution_contract_drain_evidence_id(
            cutover_id,
            effect_ledger_sha256,
            state_ledger_sha256,
            ticket_ledger_sha256,
        ),
        cutover_id=cutover_id,
        migration_epoch=gateway_epoch,
        gateway_epoch=gateway_epoch,
        from_schema_version=EXECUTION_CONTRACT_FROM_SCHEMA_VERSION,
        to_schema_version=EXECUTION_CONTRACT_TO_SCHEMA_VERSION,
        inflight_execution_count=0,
        unclaimed_ticket_count=0,
        unresolved_ambiguous_count=0,
        open_vold_intent_count=0,
        effect_ledger_sha256=effect_ledger_sha256,
        state_ledger_sha256=state_ledger_sha256,
        ticket_ledger_sha256=ticket_ledger_sha256,
        observed_at_ms=observed_at_ms,
        evidence_sha256="0" * 64,
    ).with_computed_sha256()


class ExecutionContractCutoverSnapshot(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ExecutionContractCutoverSnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    cutover_id: str = Field(pattern=r"^cut_[0-9a-f]{64}$")
    migration_epoch: int = Field(ge=1)
    gateway_epoch: int = Field(ge=1)
    from_schema_version: Literal["tiangong.gateway.contracts.v1"]
    to_schema_version: Literal["tiangong.gateway.contracts.v2"]
    state: ExecutionContractCutoverState
    revision: int = Field(ge=1, le=5)
    drain_evidence_id: str | None = Field(default=None, pattern=r"^ecx_[0-9a-f]{64}$")
    drain_evidence_sha256: Sha256 | None = None
    old_head_sha256: Sha256 | None = None
    terminal_fence_event_ids_sha256: Sha256 | None = None
    activated_at_ms: int | None = Field(default=None, ge=0)
    started_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.migration_epoch != self.gateway_epoch:
            raise ValueError("execution contract cutover must use the current single gateway epoch")
        if self.cutover_id != derive_execution_contract_cutover_id(
            self.from_schema_version,
            self.to_schema_version,
            self.gateway_epoch,
        ):
            raise ValueError("execution contract cutover identity is invalid")
        if self.updated_at_ms < self.started_at_ms:
            raise ValueError("execution contract cutover time is invalid")
        stage = _EXECUTION_CONTRACT_CUTOVER_REVISION[self.state]
        if self.revision != stage:
            raise ValueError("execution contract cutover revision is invalid")
        if (self.drain_evidence_id is None) != (self.drain_evidence_sha256 is None):
            raise ValueError("execution contract cutover drain binding is partial")
        if (stage >= 2) != (self.drain_evidence_id is not None):
            raise ValueError("execution contract cutover drain binding does not match its state")
        if (stage >= 3) != (self.old_head_sha256 is not None):
            raise ValueError("execution contract cutover old head does not match its state")
        if (stage >= 4) != (self.terminal_fence_event_ids_sha256 is not None):
            raise ValueError("execution contract cutover terminal fence does not match its state")
        if (stage >= 5) != (self.activated_at_ms is not None):
            raise ValueError("execution contract cutover activation does not match its state")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"snapshot_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.snapshot_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"snapshot_sha256": self.computed_sha256()})


def begin_execution_contract_cutover(
    *,
    gateway_epoch: int,
    started_at_ms: int,
) -> ExecutionContractCutoverSnapshot:
    """Fence vOld ingress: no new vOld intents or tickets from this point on."""

    return ExecutionContractCutoverSnapshot(
        cutover_id=derive_execution_contract_cutover_id(
            EXECUTION_CONTRACT_FROM_SCHEMA_VERSION,
            EXECUTION_CONTRACT_TO_SCHEMA_VERSION,
            gateway_epoch,
        ),
        migration_epoch=gateway_epoch,
        gateway_epoch=gateway_epoch,
        from_schema_version=EXECUTION_CONTRACT_FROM_SCHEMA_VERSION,
        to_schema_version=EXECUTION_CONTRACT_TO_SCHEMA_VERSION,
        state="FENCING",
        revision=1,
        started_at_ms=started_at_ms,
        updated_at_ms=started_at_ms,
        snapshot_sha256="0" * 64,
    ).with_computed_sha256()


def apply_execution_contract_drain(
    snapshot: ExecutionContractCutoverSnapshot,
    evidence: ExecutionContractDrainEvidence,
) -> ExecutionContractCutoverSnapshot:
    if (
        snapshot.state != "FENCING"
        or not _strictly_valid(snapshot)
        or not _strictly_valid(evidence)
        or not snapshot.has_valid_sha256()
        or not evidence.has_valid_sha256()
        or evidence.cutover_id != snapshot.cutover_id
        or evidence.gateway_epoch != snapshot.gateway_epoch
        or evidence.from_schema_version != snapshot.from_schema_version
        or evidence.to_schema_version != snapshot.to_schema_version
        or evidence.observed_at_ms < snapshot.started_at_ms
        or evidence.inflight_execution_count != 0
        or evidence.unclaimed_ticket_count != 0
        or evidence.unresolved_ambiguous_count != 0
        or evidence.open_vold_intent_count != 0
        or evidence.model_generated
    ):
        raise ValueError("execution contract drain evidence is not bound to the fencing cutover")
    return snapshot.model_copy(
        update={
            "state": "DRAINED",
            "drain_evidence_id": evidence.evidence_id,
            "drain_evidence_sha256": evidence.evidence_sha256,
            "revision": 2,
            "updated_at_ms": evidence.observed_at_ms,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()


def pin_old_head(
    snapshot: ExecutionContractCutoverSnapshot,
    evidence: ExecutionContractDrainEvidence,
) -> ExecutionContractCutoverSnapshot:
    """Pin the drained vOld state-ledger head; it is immutable once written."""

    if (
        snapshot.state != "DRAINED"
        or not _strictly_valid(snapshot)
        or not _strictly_valid(evidence)
        or not snapshot.has_valid_sha256()
        or not evidence.has_valid_sha256()
        or snapshot.drain_evidence_id != evidence.evidence_id
        or snapshot.drain_evidence_sha256 != evidence.evidence_sha256
        or snapshot.old_head_sha256 is not None
    ):
        raise ValueError("execution contract old head cannot be pinned before an exact completed drain")
    return snapshot.model_copy(
        update={
            "state": "HEAD_PINNED",
            "old_head_sha256": evidence.state_ledger_sha256,
            "revision": 3,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()


def apply_terminal_fence(
    snapshot: ExecutionContractCutoverSnapshot,
    fence_event_ids: tuple[str, ...],
) -> ExecutionContractCutoverSnapshot:
    """Record the terminal-fence pre-start writes for every non-terminal execution machine."""

    event_ids = tuple(fence_event_ids)
    if any(not isinstance(event_id, str) or not event_id for event_id in event_ids):
        raise ValueError("execution contract terminal fence event ids are invalid")
    if (
        snapshot.state != "HEAD_PINNED"
        or not _strictly_valid(snapshot)
        or not snapshot.has_valid_sha256()
        or snapshot.old_head_sha256 is None
    ):
        raise ValueError("execution contract terminal fence requires a pinned old head")
    return snapshot.model_copy(
        update={
            "state": "TERMINAL_FENCED",
            "terminal_fence_event_ids_sha256": canonical_sha256(sorted(set(event_ids))),
            "revision": 4,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()


def activate_execution_contract_epoch(
    snapshot: ExecutionContractCutoverSnapshot,
    *,
    activated_at_ms: int,
) -> ExecutionContractCutoverSnapshot:
    """Activate the vNext epoch.

    The store commits this with a compare-and-swap on ``expected_revision=4``;
    only the first concurrent activation wins, the rest fail closed.  The
    pinned ``old_head_sha256`` stays immutable and permanently auditable.
    """

    if (
        snapshot.state != "TERMINAL_FENCED"
        or snapshot.revision != 4
        or not _strictly_valid(snapshot)
        or not snapshot.has_valid_sha256()
        or snapshot.old_head_sha256 is None
        or snapshot.terminal_fence_event_ids_sha256 is None
        or activated_at_ms < snapshot.updated_at_ms
    ):
        raise ValueError("execution contract epoch activation requires a terminal-fenced revision-4 snapshot")
    return snapshot.model_copy(
        update={
            "state": "ACTIVE",
            "revision": 5,
            "activated_at_ms": activated_at_ms,
            "updated_at_ms": activated_at_ms,
            "snapshot_sha256": "0" * 64,
        }
    ).with_computed_sha256()


__all__ = [
    "ChannelCutoverSnapshot",
    "ChannelDrainEvidence",
    "ChannelOwnershipLease",
    "EXECUTION_CONTRACT_FROM_SCHEMA_VERSION",
    "EXECUTION_CONTRACT_TO_SCHEMA_VERSION",
    "ExecutionContractCutoverSnapshot",
    "ExecutionContractCutoverState",
    "ExecutionContractDrainEvidence",
    "activate_candidate_owner",
    "activate_execution_contract_epoch",
    "apply_channel_drain",
    "apply_execution_contract_drain",
    "apply_terminal_fence",
    "begin_channel_cutover",
    "begin_execution_contract_cutover",
    "build_channel_drain_evidence",
    "build_execution_contract_drain_evidence",
    "derive_cutover_id",
    "derive_execution_contract_cutover_id",
    "pin_old_head",
    "renew_candidate_owner",
]
