"""Constructor-only path contracts; no channel send or runtime dispatch."""
import os
from pathlib import Path

import pytest

from communication_service.feishu_outbound import FeishuDeliveryService
from communication_service.raw_inbound_store import RawInboundStore
from communication_service.wechat_file_outbound import WechatFileDeliveryService
from runtime_security import path_identity
from total_gateway.desktop_attachment_ingress import DesktopAttachmentIngress
from total_gateway.frozen_backend_compat import FrozenBackendCompatibilityTransport


class NoOperation:
    def __getattr__(self, name):
        raise AssertionError(f"startup must not invoke external dependency {name}")


def construct(component, root):
    dependency = NoOperation()
    if component == "raw-inbound":
        return RawInboundStore(root).root
    if component == "wechat":
        service = WechatFileDeliveryService(
            dependency, dependency, dependency, dependency,
            staging_root=root, clock_ms=lambda: 1_000, sleeper=lambda _: None,
        )
        return service._staging_root
    if component == "feishu":
        return FeishuDeliveryService(
            dependency, dependency, dependency, dependency, dependency,
            staging_root=root,
        )._staging_root
    if component == "desktop-attachment":
        return DesktopAttachmentIngress(dependency, root)._staging_root
    assert component == "compatibility"
    return FrozenBackendCompatibilityTransport(
        dependency, workspace_root=root, backend_token="", life_token="",
        backend_client=dependency, life_client=dependency,
    )._workspace_root


COMPONENTS = ("raw-inbound", "wechat", "feishu", "desktop-attachment", "compatibility")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native startup path contract")


@pytest.mark.parametrize("component", COMPONENTS)
def test_service_constructor_observes_native_path_under_dos_denial(component, tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("controlled DOS lookup denial")

    root = tmp_path / component
    root.mkdir()
    expected = root.resolve(strict=True)
    with monkeypatch.context() as fault:
        fault.setattr(Path, "resolve", denied)
        assert construct(component, root) == expected
    assert not tuple(root.iterdir())


@pytest.mark.parametrize("component", COMPONENTS)
def test_service_constructor_fails_closed_on_native_identity_denial(component, tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError("native startup identity unavailable")

    monkeypatch.setattr(path_identity, "_windows_final_path", denied)
    root = tmp_path / component
    with pytest.raises(PermissionError, match="native startup identity unavailable"):
        construct(component, root)
    assert not tuple(root.iterdir())
