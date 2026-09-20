"""Operator-configured Tool-source revision resolver for the World chain.

Verifies the published bundle bytes through the ORIGINAL P8 reader, compiles
the exact Tool Capability World from the measured manifest, and derives the
``VerifiedToolUpdate`` for the production runtime. It is configuration, not a
registry or a planner route; the derived world stays non-authorizing data and
the update never carries a model field.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from world_understanding.tool_capability_world.publication import (
    VerifiedToolUpdate,
    compute_tool_changed_source_keys,
)
from contracts.world_understanding.ingress import WorldIngressEnvelope
from world_understanding.world_state.store import MaterializedWorldSnapshot

from .tool_source_bundle import _read_verified_bundle
from .tool_source_candidate import _strict_pairs, _invalid_constant
from .tool_source_inputs import ToolSourceInputFileV1, ToolSourceInputsV1
from .tool_source_world import compile_source_bound_tool_world


class ToolSourceRevisionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class ToolSourcePublicationResolver:
    """Resolves one bundle digest to a verified Tool world update."""

    bundle_root: Path
    action_entry_path: str
    # Operator-pinned frame coordinates matching the published bundle's Git
    # workspace stream. frame_id derives from scope+workspace+repository+
    # worktree+branch, so a new candidate commit keeps the SAME stream and
    # the update invalidates in place instead of forking the world.
    workspace: str = ""
    repository: str = ""
    worktree: str = ""
    branch: str = ""
    commit: str = ""
    environment: str = ""

    def _bundle_path(self, digest: str) -> Path:
        candidate = self.bundle_root / f"{digest}.tgb"
        if not candidate.is_file():
            raise ToolSourceRevisionError(
                "tool_publication.bundle_missing", str(candidate))
        return candidate

    def __call__(self, envelope: WorldIngressEnvelope,
                 previous: MaterializedWorldSnapshot) -> VerifiedToolUpdate:
        payload = envelope.payload_inline
        digest = payload["bundle_sha256"]
        path = self._bundle_path(digest)
        raw, _index = _read_verified_bundle(path, expected_sha256=digest)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            report = json.loads(archive.read("build-report.json"),
                                object_pairs_hook=_strict_pairs,
                                parse_constant=_invalid_constant)
        artifact = report["build_artifact"]
        inputs = artifact["source_inputs"]
        measured = ToolSourceInputsV1(**{
            **inputs,
            "files": tuple(ToolSourceInputFileV1(**item) for item in inputs["files"]),
        })
        entry = next((item for item in measured.files
                      if item.path == self.action_entry_path), None)
        if entry is None:
            raise ToolSourceRevisionError(
                "tool_publication.entry_not_measured", self.action_entry_path)
        manifest = artifact["gateway_manifest"]
        tool_world = compile_source_bound_tool_world(
            manifest, measured,
            action_source_binding={"path": entry.path, "sha256": entry.content_sha256},
        )
        from world_understanding.software_world import SoftwareWorldFrame
        frame = SoftwareWorldFrame.build(
            scope=envelope.scope_hint, workspace=self.workspace,
            repository=self.repository, worktree=self.worktree,
            branch=self.branch, commit=self.commit,
            environment=self.environment, time=envelope.source_time,
        )
        if frame.frame_id != previous.frame_id:
            raise ToolSourceRevisionError(
                "tool_publication.frame_stream_mismatch",
                f"resolver={frame.frame_id} previous={previous.frame_id}")
        return VerifiedToolUpdate(
            frame=frame,
            expected_state_ref=previous.state_ref,
            bundle_sha256=digest,
            tool_world=tool_world,
            changed_source_keys=compute_tool_changed_source_keys(previous, tool_world),
        )

    def load(self, state: MaterializedWorldSnapshot):  # protocol parity
        raise ToolSourceRevisionError(
            "tool_publication.load_unsupported",
            "the sealed-plan reader owns Tool source reads")


__all__ = ["ToolSourcePublicationResolver", "ToolSourceRevisionError"]
