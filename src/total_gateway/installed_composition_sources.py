"""Project the installed source checkout into the existing composition readers.

This is an installed-source observation, not a P8 build approval or a signed P9
publication. It archives the real installed bytes and reviewed P3 method seeds;
Policy, Tickets, Grants, and the original composition admission remain mandatory.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import time
import zipfile

from contracts import ActionRegistrySnapshot, canonical_json_bytes, canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from contracts.world_understanding.repository import RepositoryObservation
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.world_cut import SourceWatermark, WorldCut, derive_world_cut_id
from world_understanding.domain_contribution import compile_skill_method_contribution, compile_tool_capability_contribution
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.skill_method_world.production_catalog import (
    compile_dictionary_skill_method_world,
)
from world_understanding.skill_method_world.publication import method_marker
from world_understanding.software_world import SoftwareWorldFrame, SparseWorldGraph
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.tool_capability_world import compile_tool_capability_world
from world_understanding.world_state import MaterializationInput
from world_understanding.world_state.domain_contributions import materialize_one_world_state

from .action_registry import compile_action_authority
from .method_source_publication import _snapshot_load, _snapshot_payload
from .method_source_run_binding import MethodRunSourceResolver
from .tool_source_inputs import compile_tool_source_inputs

SCHEMA = "tiangong.installed-composition-sources.v1"
ARCHIVE_MARKER = "installed-composition.archive"
_INDEX = "dictionaries/skills/catalog.json"
_ENTRY = "src/omni_body_skill/tools/omni_body_tool.py"
_MAX_ARCHIVE = 128 * 1024 * 1024


def _installed_inputs(root: Path):
    # P8 measures clean private build snapshots. An installed Python tree also
    # has disposable import caches; keep frozen authoritative .pyc outside those
    # directories, but never let creating a __pycache__ invalidate installed code.
    observed = compile_tool_source_inputs(root)
    cache_dirs = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    files = tuple(row for row in observed.files if not cache_dirs.intersection(Path(row.path).parts))
    draft = replace(observed, files=files, source_inputs_sha256="0" * 64)
    return replace(draft, source_inputs_sha256=canonical_sha256(draft.payload()))


def _archive(path: Path, digest: str) -> tuple[dict, zipfile.ZipFile]:
    if (not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1
            or path.stat().st_mode & 0o222 or path.stat().st_size > _MAX_ARCHIVE):
        raise ValueError("INSTALLED_SOURCE_ARCHIVE_UNSAFE")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("INSTALLED_SOURCE_ARCHIVE_CHANGED")
    archive = zipfile.ZipFile(io.BytesIO(raw))
    payload = json.loads(archive.read("installation.json"))
    if payload.get("schema") != SCHEMA or payload.get("may_authorize") is not False:
        archive.close()
        raise ValueError("INSTALLED_SOURCE_ARCHIVE_SCHEMA_INVALID")
    return payload, archive


def _tool_world(payload: dict, registry: ActionRegistrySnapshot):
    authority = compile_action_authority(payload["manifest"], generated_at_ms=registry.generated_at_ms)
    if authority.registry != registry:
        raise ValueError("COMPOSITION_SOURCE_SYSTEM_REGISTRY_MISMATCH")
    measured = payload["source_inputs"]
    entry = next(row for row in measured["files"] if row["path"] == _ENTRY)
    sources = {}
    for permission in registry.permissions:
        revision = canonical_sha256({"domain": SCHEMA, "action": permission.action_id,
            "source_inputs_sha256": measured["source_inputs_sha256"], "entry": entry})
        sources[permission.action_id] = SourceRevisionRefV1(
            source_kind="TOOL_ACTION", semantic_id=permission.action_id,
            version=permission.action_version, source_files=(_ENTRY,), source_sha256=revision,
            descriptor_sha256=canonical_sha256({"revision": revision,
                "manifest": authority.manifest_sha256}), manifest_sha256=authority.manifest_sha256)
    return compile_tool_capability_world(authority.manifest, registry,
        source_revisions=sources, action_schema_catalog=authority.schema_catalog)


@dataclass(frozen=True, slots=True)
class InstalledPlanningToolSource:
    """Exact accepted installed-source type; never constructed from model output."""
    source_root: Path
    archive_path: Path
    archive_sha256: str
    repository_id: str
    worktree_id: str
    candidate_commit: str

    def identity(self) -> str:
        return canonical_sha256({"schema": SCHEMA, "archive_sha256": self.archive_sha256,
            "source_root": str(self.source_root), "repository_id": self.repository_id,
            "worktree_id": self.worktree_id, "candidate_commit": self.candidate_commit})

    def load(self, registry: ActionRegistrySnapshot):
        payload, archive = _archive(self.archive_path, self.archive_sha256)
        with archive:
            if _installed_inputs(self.source_root).source_inputs_sha256 != payload["source_inputs"]["source_inputs_sha256"]:
                raise ValueError("INSTALLED_SOURCE_CHECKOUT_CHANGED_RESTART_REQUIRED")
            # This digest identifies observation evidence, never publication approval.
            return _tool_world(payload, registry), self.archive_sha256


@dataclass(frozen=True, slots=True)
class InstalledMethodResolver:
    archive_root: Path

    def __call__(self, envelope, previous):
        raise ValueError("INSTALLED_METHOD_SOURCE_HAS_NO_PUBLICATION_AUTHORITY")

    def load(self, state):
        digest = method_marker(state, ARCHIVE_MARKER)
        if not digest or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("INSTALLED_METHOD_SOURCE_STATE_UNBOUND")
        payload, archive = _archive(self.archive_root / (digest + ".zip"), digest)
        with archive:
            methods = _snapshot_load(payload["methods"])
        if method_marker(state, "method-world.snapshot") != methods.snapshot_sha256:
            raise ValueError("INSTALLED_METHOD_SOURCE_SNAPSHOT_MISMATCH")
        return methods


@dataclass(frozen=True, slots=True)
class InstalledCompositionSources:
    runtime: ProductionWorldUnderstandingRuntime
    resolver: MethodRunSourceResolver
    tool_source: InstalledPlanningToolSource
    registry: ActionRegistrySnapshot
    observation: RepositoryObservation

    @classmethod
    def install(cls, *, runtime, gateway, source_root: Path, archive_root: Path,
                registry: ActionRegistrySnapshot, manifest: dict,
                repository_observation: RepositoryObservation | None = None):
        if type(runtime) is not ProductionWorldUnderstandingRuntime:
            raise TypeError("INSTALLED_SOURCE_EXISTING_WORLD_REQUIRED")
        source_root = Path(source_root).resolve(strict=True)
        archive_root = Path(archive_root).resolve(strict=False)
        if source_root == archive_root or source_root in archive_root.parents:
            raise ValueError("INSTALLED_SOURCE_ARCHIVE_MUST_BE_OUTSIDE_CHECKOUT")
        authority = compile_action_authority(manifest, generated_at_ms=registry.generated_at_ms)
        if authority.registry != registry:
            raise ValueError("COMPOSITION_SOURCE_SYSTEM_REGISTRY_MISMATCH")
        if repository_observation is None:
            from v3.repository_perception import LocalGitRepositoryProvider
            provider = LocalGitRepositoryProvider()
            identity = provider.discover(str(source_root))
            if identity is None:
                raise ValueError("INSTALLED_SOURCE_REPOSITORY_REQUIRED")
            repository_observation = provider.observe(identity)
        observation = repository_observation
        if (type(observation) is not RepositoryObservation
                or Path(observation.identity.worktree_root_ref).resolve() != source_root):
            raise ValueError("INSTALLED_SOURCE_REPOSITORY_MISMATCH")
        inputs = _installed_inputs(source_root)
        files = {item.path: (source_root / item.path).read_bytes() for item in inputs.files}
        if any(hashlib.sha256(files[item.path]).hexdigest() != item.content_sha256 for item in inputs.files):
            raise ValueError("INSTALLED_SOURCE_CHANGED_DURING_ARCHIVE")
        methods = compile_dictionary_skill_method_world(files, observation_sha256=inputs.source_inputs_sha256)
        payload = {"schema": SCHEMA, "provenance": "INSTALLED_SOURCE_OBSERVATION",
            "source_inputs": asdict(inputs), "manifest": authority.manifest,
            "methods": _snapshot_payload(methods), "method_seeds_sha256": canonical_sha256({p: hashlib.sha256(raw).hexdigest()
                for p, raw in files.items() if p.startswith("dictionaries/skills/methods/")}),
            "repository_identity": observation.identity.model_dump(mode="json"),
            "repository_revision": {"branch": observation.revision.branch,
                "head_commit": observation.revision.head_commit,
                "working_tree_sha256": observation.working_tree_state.state_sha256},
            "may_authorize": False, "may_execute": False, "publication_approval": False}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in [("installation.json", canonical_json_bytes(payload)), *sorted(files.items())]:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, raw)
        raw = stream.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        archive_root.mkdir(parents=True, exist_ok=True)
        path = archive_root / (digest + ".zip")
        try:
            with path.open("xb") as target:
                target.write(raw); target.flush(); os.fsync(target.fileno())
            path.chmod(0o444)
        except FileExistsError:
            pass
        _, check = _archive(path, digest)
        check.close()
        source = InstalledPlanningToolSource(source_root, path, digest,
            observation.identity.repository_id, observation.identity.worktree_id,
            observation.revision.head_commit)
        method_reader = InstalledMethodResolver(archive_root)
        previous_reader = runtime._method_revision_resolver
        if previous_reader is None:
            runtime.install_method_revision_resolver(method_reader)
        elif previous_reader != method_reader:
            raise ValueError("INSTALLED_SOURCE_METHOD_READER_ALREADY_CONFIGURED")
        resolver = MethodRunSourceResolver(gateway, runtime)
        gateway.configure_method_source_lifecycle(resolver)
        return cls(runtime, resolver, source, registry, observation)

    def ensure_world(self, run_context, *, now_ms: int | None = None):
        """Publish both descriptions at one exact cut on the existing WorldStore."""
        context = run_context
        gateway = self.resolver.gateway
        generation = gateway.get_request_generation_binding(context.request_id)
        envelope = gateway.get_request_envelope(context.request_id)
        if (generation is None or generation["status"] != "ACTIVE"
                or generation["run_id"] != context.run_id or generation["current_generation"] != context.generation
                or envelope is None or envelope.principal_scope_hash != context.principal_scope_hash):
            raise ValueError("INSTALLED_SOURCE_REQUEST_SCOPE_UNAVAILABLE")
        bindings = (ScopeBinding(key="frame_kind", value="v3_runtime_workspace"),
                    ScopeBinding(key="workspace_id", value=context.workspace_id))
        world_id = derive_world_id(life_id=context.life_id, namespace_anchor="workspace:" + context.workspace_id)
        scope = WorldScope(life_id=context.life_id, world_id=world_id, domain_id="software_runtime",
            scope_bindings=bindings, world_scope_hash=derive_world_scope_hash(life_id=context.life_id,
                world_id=world_id, domain_id="software_runtime", scope_bindings=bindings),
            principal_scope_hash=context.principal_scope_hash, privacy_scope="system")
        timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        observed_time = WorldTime(valid_from_ms=timestamp, observed_at_ms=timestamp, recorded_at_ms=timestamp)
        native_id = "installed-source." + self.tool_source.archive_sha256
        source = build_post_commit_source_envelope(source_kind="GIT_CODE", source_native_id=native_id,
            producer_ref="total_gateway.installed_composition_sources",
            payload={"repository_observation": self.observation.model_dump(mode="json")},
            source_time=observed_time, scope=scope, correlation_id=native_id,
            workspace_id=context.workspace_id)
        tools, _ = self.tool_source.load(self.registry)
        payload, archive = _archive(self.tool_source.archive_path, self.tool_source.archive_sha256)
        with archive:
            methods = _snapshot_load(payload["methods"])
        runtime = self.runtime
        with runtime._lock:
            probe = runtime.frame_factory(source, None)
            if (probe.repository != self.tool_source.repository_id or probe.worktree != self.tool_source.worktree_id
                    or probe.commit != self.tool_source.candidate_commit):
                raise ValueError("INSTALLED_SOURCE_FRAME_FACTORY_MISMATCH")
            previous = runtime._previous(probe)
            if previous is None:
                receipt = runtime.facade.accept(source)
                if not receipt.processed:
                    raise ValueError("INSTALLED_SOURCE_GENESIS_UNAVAILABLE")
                previous = runtime._previous(probe)
            if previous is None:
                raise ValueError("INSTALLED_SOURCE_WORLD_UNAVAILABLE")
            if (method_marker(previous, ARCHIVE_MARKER) == self.tool_source.archive_sha256
                    and method_marker(previous, "tool-world.snapshot") == tools.snapshot_sha256
                    and method_marker(previous, "method-world.snapshot") == methods.snapshot_sha256
                    and any(e.entity_type == "ToolCapability" for e in previous.entities)
                    and any(e.entity_type == "SkillMethod" for e in previous.entities)):
                # Reuse only if every descriptor is still bound to the current cut.
                from world_understanding.context_output.world_reference_context import _address
                try:
                    for entity in previous.entities:
                        if entity.entity_type in {"ToolCapability", "SkillMethod"}:
                            _address(entity, previous)
                    return previous
                except ValueError:
                    pass
            with runtime.store.publication_transaction(previous):
                marks = {(w.source_kind, w.watermark_type): w for w in previous.cut.source_watermarks}
                for name, digest in ((ARCHIVE_MARKER, self.tool_source.archive_sha256),
                        ("method-world.snapshot", methods.snapshot_sha256), ("tool-world.snapshot", tools.snapshot_sha256)):
                    key = ("SYSTEM_GOVERNANCE", name)
                    old = marks.get(key)
                    marks[key] = SourceWatermark(source_kind=key[0], watermark_type=name,
                        watermark_value=digest, sequence=0 if old is None else (old.sequence or 0) + 1,
                        watermark_sha256="0" * 64).with_computed_hash()
                rows = tuple(sorted(marks.values(), key=lambda w: w.sort_key()))
                cut = WorldCut(cut_id=derive_world_cut_id(world_scope_hash=scope.world_scope_hash, watermarks=rows),
                    scope=scope, source_watermarks=rows, time=observed_time, cut_sha256="0" * 64).with_computed_hash()
                frame = SoftwareWorldFrame.build(scope=scope, workspace=context.workspace_id,
                    repository=probe.repository, worktree=probe.worktree, branch=probe.branch,
                    commit=probe.commit, environment=platform.system().lower(), time=observed_time, world_cut=cut)
                graph = SparseWorldGraph(frame)
                replaced_entities = {e.entity_id for e in previous.entities
                    if e.entity_type in {"ToolCapability", "SkillMethod"}}
                replaced_relations = {r.relation_id for r in previous.relations
                    if r.subject_ref.record_id in replaced_entities
                    and r.predicate.startswith(("tool.", "method."))}
                for entity in previous.entities:
                    if entity.entity_id not in replaced_entities:
                        graph.upsert_entity(entity)
                for relation in previous.relations:
                    if relation.relation_id not in replaced_relations:
                        graph.upsert_relation(relation)
                dependencies = tuple(binding for binding in previous.dependencies.bindings
                    if not (binding.ref.record_type == "world_entity" and binding.ref.record_id in replaced_entities)
                    and not (binding.ref.record_type == "world_relation" and binding.ref.record_id in replaced_relations))
                changed_keys = set()
                for marker, prefix, digest in (("tool-world.snapshot", "tool-world:", tools.snapshot_sha256),
                        ("method-world.snapshot", "method-world:", methods.snapshot_sha256)):
                    old = method_marker(previous, marker)
                    if old != digest:
                        changed_keys.add(prefix + digest)
                        if old:
                            changed_keys.add(prefix + old)
                old_entities = {e.entity_id: e for e in previous.entities}
                old_relations = {r.relation_id: r for r in previous.relations}
                contributions = (compile_tool_capability_contribution(frame, cut, tools,
                    previous_entities=old_entities, previous_relations=old_relations),
                    compile_skill_method_contribution(frame, cut, methods,
                    previous_entities=old_entities, previous_relations=old_relations))
                snapshot = materialize_one_world_state(runtime._materializer,
                    MaterializationInput(frame=frame, cut=cut, graph=graph,
                        dependency_bindings=dependencies, changed_source_keys=tuple(sorted(changed_keys)),
                        preserve_previous_domains=True, source_transaction_id=native_id,
                        materialized_at_ms=timestamp), contributions)
                runtime._restore_live_stream(source, snapshot)
                return snapshot


__all__ = ["InstalledCompositionSources", "InstalledPlanningToolSource", "InstalledMethodResolver"]
