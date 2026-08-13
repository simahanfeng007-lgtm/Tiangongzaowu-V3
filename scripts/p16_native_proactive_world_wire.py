from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def patch_life_world_read() -> None:
    rel = "src/life_service/embedded_runtime.py"
    text = read(rel)
    old = '''        provider = self._proactive_world_provider
        if not callable(provider):
            return []
        try:
            snapshot = provider(life_id)
        except Exception:
            return []
'''
    new = '''        provider = self._proactive_world_provider
        identity_provider = self._world_identity_provider
        if not callable(provider) or not callable(identity_provider):
            return []
        try:
            supplied_identity = identity_provider(life_id)
            if not isinstance(supplied_identity, Mapping):
                return []
            identity = {
                key: str(value or "")
                for key, value in supplied_identity.items()
                if str(key) in {"life_id", "principal_scope_hash", "workspace_id"}
            }
            if not identity.get("life_id") or not identity.get("principal_scope_hash") or not identity.get("workspace_id"):
                return []
            snapshot = provider(identity)
        except Exception:
            return []
'''
    text = replace_once(text, old, new, "world provider exact-scope identity")
    write(rel, text)


def patch_gateway_world_wire() -> None:
    rel = "src/total_gateway/runtime.py"
    text = read(rel)
    old = '''                runtime.life_service.set_world_identity_provider(
                    lambda life_id: {
                        "life_id": str(life_id),
                        "principal_scope_hash": canonical_sha256({
                            "domain": "tiangong.life.self-reality-principal.v1",
                            "life_id": str(life_id),
                        }),
                        "workspace_id": "workspace-" + canonical_sha256(str(config.workspace_root)),
                    }
                )
                runtime.life_service.set_capability_workspace_mapper(
'''
    new = '''                runtime.life_service.set_world_identity_provider(
                    lambda life_id: {
                        "life_id": str(life_id),
                        "principal_scope_hash": canonical_sha256({
                            "domain": "tiangong.life.self-reality-principal.v1",
                            "life_id": str(life_id),
                        }),
                        "workspace_id": "workspace-" + canonical_sha256(str(config.workspace_root)),
                    }
                )
                # P16 reads World only through the existing committed WU projection.
                # This callback performs no sensing and creates no second World runtime.
                runtime.life_service.set_proactive_world_provider(
                    runtime.backend_service.repository_evidence_snapshot
                )
                runtime.life_service.set_capability_workspace_mapper(
'''
    text = replace_once(text, old, new, "gateway committed world provider wire")
    write(rel, text)


def patch_tests() -> None:
    rel = "tests/test_p16_native_proactive_runtime.py"
    text = read(rel)
    text = text.replace(
        'life.set_proactive_world_provider(lambda _life_id: {"schema": "untrusted", "observed_at_ms": NOW})',
        'life.set_world_identity_provider(lambda life_id: {"life_id": life_id, "principal_scope_hash": "p" * 64, "workspace_id": "workspace-test"})\n            life.set_proactive_world_provider(lambda _identity: {"schema": "untrusted", "observed_at_ms": NOW})',
    )
    text = text.replace(
        'life.set_proactive_world_provider(lambda _life_id: {\n                "schema": "tiangong.life.repository-evidence.v1",',
        'life.set_proactive_world_provider(lambda _identity: {\n                "schema": "tiangong.life.repository-evidence.v1",',
    )
    if "test_gateway_wires_proactive_world_to_existing_committed_wu_reader" not in text:
        text += r'''


def test_gateway_wires_proactive_world_to_existing_committed_wu_reader():
    source = Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "runtime.py"
    gateway = source.read_text(encoding="utf-8")
    assert "set_proactive_world_provider" in gateway
    assert "runtime.backend_service.repository_evidence_snapshot" in gateway
    backend = (Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "embedded_backend.py").read_text(encoding="utf-8")
    reader = backend.split("def repository_evidence_snapshot", 1)[1].split("\n    def ", 1)[0]
    assert "production_repository_evidence_snapshot" in reader
    assert "world_understanding_production" in reader
'''
    write(rel, text)


if __name__ == "__main__":
    patch_life_world_read()
    patch_gateway_world_wire()
    patch_tests()
    print("P16 committed World Understanding read path wired")
