from __future__ import annotations

import importlib.util
import json
import multiprocessing
import sys
from pathlib import Path


def _backend_overlay_present() -> bool:
    roots = (
        Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)),
        Path(sys.executable).resolve().parent,
    )
    return any((root / "backend" / "tiangong-backend" / "v3").is_dir() for root in roots)


def _release_probe() -> int:
    from communication_service.embedded_runtime import EmbeddedCommunicationService
    from communication_service.wechat_login import WECHAT_ILINK_ORIGIN
    from life_service.embedded_runtime import LIFE_API_CONTRACT, EmbeddedLifeRuntime
    from life_service.identity_migration import migrate_legacy_identities
    from total_gateway import release_manifest, server
    from total_gateway.embedded_backend import EmbeddedBackendRuntime

    lark_available = importlib.util.find_spec("lark_oapi") is not None
    payload = {
        "component_id": "tiangong-total-gateway",
        "ok": bool(
            callable(server.run_gateway)
            and EmbeddedBackendRuntime is not None
            and EmbeddedLifeRuntime is not None
            and EmbeddedCommunicationService is not None
            and _backend_overlay_present()
            and lark_available
        ),
        "deployment_mode": "embedded",
        "listener_port": 7184,
        "physical_python_processes": 1,
        "runtime_api_contract": "tiangong.desktop.backend.v3",
        "life_api_contract": LIFE_API_CONTRACT,
        "communication_api_contract": "tiangong.communication.api.v1",
        "identity_migration": callable(migrate_legacy_identities),
        "wechat_qr": WECHAT_ILINK_ORIGIN.startswith("https://"),
        "lark_oapi": lark_available,
        "backend_overlay": _backend_overlay_present(),
        "release_manifest": release_manifest.RELEASE_MANIFEST_FILENAME,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["ok"] else 1


def main() -> int:
    multiprocessing.freeze_support()
    if "--release-probe" in sys.argv[1:]:
        return _release_probe()
    from total_gateway.__main__ import main as gateway_main

    return gateway_main()


if __name__ == "__main__":
    raise SystemExit(main())
