"""Production readiness evidence bound to the selected release authority."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from contracts import (
    ComponentReadinessEvidence,
    ReleaseManifest,
    ReadinessExpectation,
    readiness_expectation_from_manifest,
)


_REQUIRED_COMPONENT_IDS = (
    "tiangong-backend",
    "tiangong-communication-service",
    "tiangong-life-service",
    "tiangong-total-gateway",
)
_SAFE_INSTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_MAX_HEALTH_BYTES = 256 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("readiness health response contains a duplicate key")
        value[key] = item
    return value


class ProductionReadinessCollector:
    """Collect release-bound, non-model evidence for the four service processes.

    Production remains the default and fail-closed mode.  Reconstructed source
    launches may opt in to development evidence explicitly; the same component
    hashes, authenticated loopback probes, gateway epoch, and readiness contract
    are still enforced, but the result is not represented as a production claim.
    """

    def __init__(
        self,
        *,
        release: ReleaseManifest,
        release_manifest_path: Path,
        gateway_epoch: int,
        gateway_instance_id: str,
        backend_token: str,
        life_token: str,
        communication_token: str,
        probe_timeout_seconds: float = 0.75,
        binary_recheck_seconds: float = 60.0,
        allow_development_release: bool = False,
        embedded_services: Mapping[str, object] | None = None,
    ) -> None:
        if not release.has_valid_release_manifest_sha256():
            raise ValueError("readiness requires a verified release manifest")
        if not release.production_claim and not allow_development_release:
            raise ValueError("production readiness requires a production release")
        if not release_manifest_path.is_absolute() or not release_manifest_path.is_file():
            raise ValueError("production readiness manifest path is unavailable")
        if not 0.1 <= probe_timeout_seconds <= 3.0:
            raise ValueError("production readiness probe timeout is unsafe")
        if not 5.0 <= binary_recheck_seconds <= 3600.0:
            raise ValueError("production readiness binary recheck interval is unsafe")
        self._release = release
        self._evidence_profile = "production" if release.production_claim else "development-source"
        self._manifest_path = release_manifest_path.resolve(strict=True)
        self._runtime_root = self._resolve_runtime_root(self._manifest_path, release)
        self._gateway_epoch = gateway_epoch
        self._gateway_instance_id = gateway_instance_id
        self._tokens = {
            "tiangong-backend": backend_token,
            "tiangong-life-service": life_token,
            "tiangong-communication-service": communication_token,
        }
        self._embedded_services = dict(embedded_services or {})
        missing_tokens = [
            component_id
            for component_id, value in self._tokens.items()
            if component_id not in self._embedded_services and len(value) < 32
        ]
        if missing_tokens:
            raise ValueError("production readiness token is unavailable")
        self._timeout = probe_timeout_seconds
        self._binary_recheck_seconds = binary_recheck_seconds
        self._lock = threading.Lock()
        self._observed_binary_sha256: dict[str, str] = {}
        self._binary_verified: frozenset[str] = frozenset()
        self._last_binary_check_ns = 0
        self.expectation = readiness_expectation_from_manifest(
            release.component_manifest,
            expectation_id=f"release-{release.release_manifest_sha256[:24]}",
            gateway_epoch=gateway_epoch,
            contract_artifact_manifest_sha256=(
                release.contract_artifact_manifest_sha256
            ),
            allow_development_manifest=allow_development_release,
        )
        self._refresh_binary_evidence(force=True)

    @property
    def evidence_profile(self) -> str:
        return self._evidence_profile

    @staticmethod
    def _resolve_runtime_root(manifest_path: Path, release: ReleaseManifest) -> Path:
        required = {
            item.component_id: item
            for item in release.component_manifest.components
            if item.component_id in _REQUIRED_COMPONENT_IDS
        }
        if set(required) != set(_REQUIRED_COMPONENT_IDS):
            raise ValueError("readiness release is missing a required component")
        candidates = (manifest_path.parent.parent, manifest_path.parent.parent.parent)
        for raw_root in candidates:
            try:
                root = raw_root.resolve(strict=True)
            except OSError:
                continue
            valid = True
            for descriptor in required.values():
                candidate = (root / Path(descriptor.executable_relative_path)).resolve(strict=False)
                if root not in candidate.parents or not candidate.is_file() or candidate.is_symlink():
                    valid = False
                    break
            if valid:
                return root
        raise ValueError("readiness runtime root cannot be bound to the selected manifest")

    def _component_file(self, relative_path: str) -> Path:
        candidate = (self._runtime_root / Path(relative_path)).resolve(strict=False)
        if self._runtime_root not in candidate.parents:
            raise ValueError("component executable escaped the runtime root")
        if not candidate.is_file() or candidate.is_symlink():
            raise FileNotFoundError("component executable is missing or unsafe")
        return candidate

    def _refresh_binary_evidence(self, *, force: bool = False) -> None:
        current_ns = time.monotonic_ns()
        if (
            not force
            and self._last_binary_check_ns
            and (current_ns - self._last_binary_check_ns) / 1_000_000_000
            < self._binary_recheck_seconds
        ):
            return
        descriptors = {
            item.component_id: item
            for item in self._release.component_manifest.components
            if item.component_id in _REQUIRED_COMPONENT_IDS
        }
        observed: dict[str, str] = {}
        verified: set[str] = set()
        for component_id in _REQUIRED_COMPONENT_IDS:
            descriptor = descriptors.get(component_id)
            digest = "0" * 64
            if descriptor is not None:
                try:
                    path = self._component_file(descriptor.executable_relative_path)
                    if path.stat().st_size == descriptor.size_bytes:
                        digest = _sha256_file(path)
                except (OSError, ValueError):
                    digest = "0" * 64
                if digest == descriptor.sha256:
                    verified.add(component_id)
            observed[component_id] = digest
        self._observed_binary_sha256 = observed
        self._binary_verified = frozenset(verified)
        self._last_binary_check_ns = current_ns

    def _request_health(
        self,
        component_id: str,
        *,
        port: int,
        path: str,
        header_name: str,
    ) -> tuple[bool, int, dict[str, Any]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            port,
            timeout=self._timeout,
        )
        response = None
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    header_name: self._tokens[component_id],
                },
            )
            response = connection.getresponse()
            raw = response.read(_MAX_HEALTH_BYTES + 1)
            if len(raw) > _MAX_HEALTH_BYTES:
                return False, response.status, {}
            payload = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if not isinstance(payload, dict):
                return False, response.status, {}
            authenticated = response.status not in {401, 403}
            return authenticated, response.status, payload
        except Exception:
            return False, 0, {}
        finally:
            if response is not None:
                response.close()
            connection.close()

    @staticmethod
    def _instance_id(payload: dict[str, Any], component_id: str) -> str:
        candidate = payload.get("instance_id")
        if isinstance(candidate, str) and _SAFE_INSTANCE_ID.fullmatch(candidate):
            return candidate
        return f"verified-{component_id}"

    def _probe_component(self, component_id: str) -> tuple[bool, bool, str]:
        if component_id == "tiangong-total-gateway":
            return True, True, self._gateway_instance_id
        descriptor = next(
            item
            for item in self._release.component_manifest.components
            if item.component_id == component_id
        )
        embedded = self._embedded_services.get(component_id)
        if embedded is not None:
            try:
                # An embedded logical component must be described as an
                # embedded component by the selected release authority.  This
                # makes an old 7174/7175/7176 manifest fail closed instead of
                # blessing a different in-process implementation.
                topology_bound = not descriptor.ports
                if component_id == "tiangong-communication-service":
                    status, payload = embedded.ready_payload(now_ms=time.time_ns() // 1_000_000)
                    passed = (
                        topology_bound
                        and status == 200
                        and payload.get("ok") is True
                        and payload.get("status") == "READY"
                        and payload.get("component_id") == component_id
                        and payload.get("build_id") == descriptor.build_id
                        and payload.get("api_contract") in descriptor.api_contract_ids
                        and payload.get("deployment_mode") == "embedded"
                        and payload.get("listener_port") is None
                    )
                else:
                    payload = embedded.health_payload()
                    if component_id == "tiangong-backend":
                        passed = (
                            topology_bound
                            and payload.get("ok") is True
                            and payload.get("component_id") == component_id
                            and payload.get("bridge_ready") is True
                            and payload.get("build_id") == descriptor.build_id
                            and payload.get("api_contract_version") in descriptor.api_contract_ids
                            and payload.get("deployment_mode") == "embedded"
                            and payload.get("listener_port") is None
                        )
                    else:
                        passed = (
                            topology_bound
                            and payload.get("ok") is True
                            and payload.get("component_id") == component_id
                            and payload.get("life_ready") is True
                            and payload.get("build_id") == descriptor.build_id
                            and payload.get("api_contract") in descriptor.api_contract_ids
                            and payload.get("deployment_mode") == "embedded"
                            and payload.get("listener_port") is None
                        )
                return True, bool(passed), self._instance_id(payload, component_id)
            except Exception:
                return True, False, f"embedded-{component_id}"
        if component_id == "tiangong-backend":
            authenticated, status, payload = self._request_health(
                component_id,
                port=7174,
                path="/health",
                header_name="X-Tiangong-Token",
            )
            passed = (
                status == 200
                and payload.get("ok") is True
                and payload.get("bridge_ready") is True
                and payload.get("build_id") == descriptor.build_id
                and payload.get("api_contract_version") in descriptor.api_contract_ids
            )
        elif component_id == "tiangong-life-service":
            authenticated, status, payload = self._request_health(
                component_id,
                port=7175,
                path="/health",
                header_name="X-Tiangong-Token",
            )
            passed = (
                status == 200
                and payload.get("ok") is True
                and payload.get("api_contract") in descriptor.api_contract_ids
            )
        else:
            authenticated, status, payload = self._request_health(
                component_id,
                port=7176,
                path="/api/v1/internal/control/readiness",
                header_name="X-Tiangong-Communication-Token",
            )
            passed = (
                status == 200
                and payload.get("ok") is True
                and payload.get("status") == "READY"
                and payload.get("component_id") == component_id
                and payload.get("api_contract") in descriptor.api_contract_ids
            )
        return authenticated, passed, self._instance_id(payload, component_id)

    def collect(
        self,
        *,
        now_ms: int,
    ) -> tuple[
        ReadinessExpectation,
        tuple[ComponentReadinessEvidence, ...],
        frozenset[str],
        frozenset[str],
    ]:
        with self._lock:
            self._refresh_binary_evidence()
            expected = {item.component_id: item for item in self.expectation.components}
            authenticated: set[str] = set()
            evidence: list[ComponentReadinessEvidence] = []
            for component_id in _REQUIRED_COMPONENT_IDS:
                transport_authenticated, health_passed, instance_id = self._probe_component(
                    component_id
                )
                if transport_authenticated:
                    authenticated.add(component_id)
                item = expected[component_id]
                evidence.append(
                    ComponentReadinessEvidence(
                        evidence_id=f"ready-{component_id}-{now_ms}",
                        component_id=component_id,
                        component_role=item.role,
                        instance_id=instance_id,
                        version=item.version,
                        build_id=item.build_id,
                        executable_sha256=self._observed_binary_sha256.get(
                            component_id,
                            "0" * 64,
                        ),
                        gateway_epoch=self._gateway_epoch,
                        component_manifest_sha256=(
                            self.expectation.component_manifest_sha256
                        ),
                        schema_bundle_sha256=self.expectation.schema_bundle_sha256,
                        capability_manifest_sha256=(
                            self.expectation.capability_manifest_sha256
                        ),
                        skill_index_sha256=self.expectation.skill_index_sha256,
                        release_policy_sha256=self.expectation.release_policy_sha256,
                        contract_artifact_manifest_sha256=(
                            self.expectation.contract_artifact_manifest_sha256
                        ),
                        health_check_passed=health_passed,
                        observed_at_ms=now_ms,
                        evidence_sha256="0" * 64,
                    ).with_computed_sha256()
                )
            return (
                self.expectation,
                tuple(evidence),
                frozenset(authenticated),
                self._binary_verified,
            )


__all__ = ["ProductionReadinessCollector"]
