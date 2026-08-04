"""Deterministic replay of immutable life-event chains."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contracts import LifeEventEnvelope, canonical_json_bytes, canonical_sha256


@dataclass(frozen=True, slots=True)
class LifeReplaySummary:
    life_id: str
    writer_epoch: int
    event_count: int
    head_event_id: str
    head_event_hash: str
    replay_sha256: str


def advance_replay_sha256(previous: str | None, event_hash: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.life.deterministic-replay.v1",
            "event_hash": event_hash,
            "previous_replay_sha256": previous,
        }
    )


def replay_life_events(events: Iterable[LifeEventEnvelope]) -> LifeReplaySummary:
    expected_sequence = 1
    life_id: str | None = None
    writer_epoch = 0
    previous_event_hash: str | None = None
    replay_sha256: str | None = None
    head_event_id: str | None = None
    for event in events:
        try:
            event = LifeEventEnvelope.model_validate_json(canonical_json_bytes(event))
        except Exception as exc:
            raise ValueError("life replay found an invalid event contract") from exc
        if not event.has_valid_event_hash():
            raise ValueError("life replay found an invalid event digest")
        if life_id is None:
            life_id = event.life_id
        if event.life_id != life_id:
            raise ValueError("life replay mixed multiple life identities")
        if event.sequence != expected_sequence:
            raise ValueError("life replay sequence is discontinuous")
        if event.previous_event_hash != previous_event_hash:
            raise ValueError("life replay hash chain is discontinuous")
        if event.writer_epoch < writer_epoch:
            raise ValueError("life replay writer epoch moved backwards")
        writer_epoch = event.writer_epoch
        previous_event_hash = event.event_hash
        replay_sha256 = advance_replay_sha256(replay_sha256, event.event_hash)
        head_event_id = event.event_id
        expected_sequence += 1
    if (
        life_id is None
        or previous_event_hash is None
        or replay_sha256 is None
        or head_event_id is None
    ):
        raise ValueError("life replay requires at least one event")
    return LifeReplaySummary(
        life_id=life_id,
        writer_epoch=writer_epoch,
        event_count=expected_sequence - 1,
        head_event_id=head_event_id,
        head_event_hash=previous_event_hash,
        replay_sha256=replay_sha256,
    )


__all__ = [
    "LifeReplaySummary",
    "advance_replay_sha256",
    "replay_life_events",
]
