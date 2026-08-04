"""Strict loopback control and delivery client for the replacement 7176."""

from __future__ import annotations

import http.client
import json
from typing import Any, Mapping

from contracts import (
    ChannelOwnershipLease,
    ComponentManifest,
    DeliveryReceipt,
    DeliveryTicket,
    OutboundPlan,
    TrustBundle,
    canonical_json_bytes,
)


class CommunicationClientError(RuntimeError):
    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CommunicationClientError("communication_client.response.duplicate_key")
        result[key] = value
    return result


class CommunicationControlClient:
    def __init__(self, token: str, *, port: int = 7176) -> None:
        if not 32 <= len(token) <= 512 or port != 7176:
            raise ValueError("communication control client configuration is invalid")
        self._token = token
        self._port = port

    def health(self) -> dict[str, Any]:
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=3.0)
        response = None
        try:
            connection.request("GET", "/health", headers={"Accept": "application/json"})
            response = connection.getresponse()
            raw = response.read(262_145)
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if (
                response.status != 200
                or not isinstance(value, dict)
                or value.get("component_id") != "tiangong-communication-service"
                or value.get("authority") != "transport_only"
                or value.get("delivery_ticket_required") is not True
                or value.get("legacy_business_dependencies_permitted") is not False
                or canonical_json_bytes(value) != raw
            ):
                raise CommunicationClientError("communication_client.health.invalid")
            return value
        except CommunicationClientError:
            raise
        except Exception as exc:
            raise CommunicationClientError("communication_client.health.unavailable") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        external_send_started: bool = False,
    ) -> dict[str, Any]:
        if not path.startswith("/api/v1/internal/") or "?" in path:
            raise CommunicationClientError("communication_client.path.forbidden")
        body = canonical_json_bytes(dict(payload))
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=timeout_seconds)
        response = None
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "X-Tiangong-Communication-Token": self._token,
                },
            )
            response = connection.getresponse()
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise CommunicationClientError(
                    "communication_client.response.too_large",
                    ambiguous=external_send_started,
                )
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "application/problem+json"}:
                raise CommunicationClientError(
                    "communication_client.response.content_type_invalid",
                    ambiguous=external_send_started,
                )
            try:
                value = json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except CommunicationClientError:
                raise
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise CommunicationClientError(
                    "communication_client.response.invalid_json",
                    ambiguous=external_send_started,
                ) from exc
            if not isinstance(value, dict):
                raise CommunicationClientError(
                    "communication_client.response.invalid_shape",
                    ambiguous=external_send_started,
                )
            if canonical_json_bytes(value) != raw:
                raise CommunicationClientError(
                    "communication_client.response.noncanonical",
                    ambiguous=external_send_started,
                )
            if response.status < 200 or response.status >= 300 or value.get("ok") is not True:
                raise CommunicationClientError(
                    str(value.get("reason_code") or "communication_client.rejected"),
                    ambiguous=(
                        external_send_started
                        and value.get("outcome_unknown") is not False
                    ),
                )
            return value
        except CommunicationClientError:
            raise
        except Exception as exc:
            raise CommunicationClientError(
                "communication_client.outcome_unknown",
                ambiguous=external_send_started,
            ) from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

    def credential_status(self) -> dict[str, Any]:
        path = "/api/v1/internal/control/credentials/status"
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5.0)
        response = None
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    "X-Tiangong-Communication-Token": self._token,
                },
            )
            response = connection.getresponse()
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise CommunicationClientError("communication_client.response.too_large")
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "application/problem+json"}:
                raise CommunicationClientError("communication_client.response.content_type_invalid")
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
                raise CommunicationClientError("communication_client.response.invalid_json")
            if response.status != 200 or value.get("ok") is not True:
                raise CommunicationClientError(
                    str(value.get("reason_code") or "communication_client.rejected")
                )
            return value
        except CommunicationClientError:
            raise
        except Exception as exc:
            raise CommunicationClientError("communication_client.status.unavailable") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

    def migrate_legacy_credentials(self) -> dict[str, Any]:
        return self._post("/api/v1/internal/control/credentials/migrate-legacy", {})

    def install_channel_lease(self, lease: ChannelOwnershipLease) -> dict[str, Any]:
        return self._post(
            "/api/v1/internal/control/lease/install",
            {"lease": lease.model_dump(mode="json")},
        )

    def install_delivery_authority(
        self,
        trust_bundle: TrustBundle,
        component_manifest: ComponentManifest,
    ) -> dict[str, Any]:
        return self._post(
            "/api/v1/internal/control/delivery/authority/install",
            {
                "component_manifest": component_manifest.model_dump(mode="json"),
                "trust_bundle": trust_bundle.model_dump(mode="json"),
            },
        )

    def channel_drain_facts(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> dict[str, Any]:
        return self._post(
            "/api/v1/internal/control/drain/facts",
            {
                "channel": channel,
                "link_account_id": link_account_id,
                "tenant_id": tenant_id,
            },
        )

    def dispatch_delivery(
        self,
        ticket: DeliveryTicket,
        plan: OutboundPlan,
    ) -> DeliveryReceipt:
        value = self._post(
            "/api/v1/internal/delivery",
            {
                "plan": plan.model_dump(mode="json"),
                "ticket": ticket.model_dump(mode="json"),
            },
            timeout_seconds=max(
                30.0,
                ticket.payload.send_timeout_ms / 1_000 + 5.0,
                ticket.payload.upload_timeout_ms / 1_000 + 30.0,
            ),
            external_send_started=True,
        )
        try:
            receipt = DeliveryReceipt.model_validate(value["delivery_receipt"], strict=True)
        except (KeyError, ValueError) as exc:
            raise CommunicationClientError(
                "communication_client.delivery_receipt.invalid",
                ambiguous=True,
            ) from exc
        if (
            not receipt.has_valid_receipt_sha256()
            or receipt.ticket_id != ticket.payload.ticket_id
            or receipt.delivery_id != ticket.payload.delivery_id
            or receipt.effect_id != ticket.payload.effect_id
            or receipt.request_id != ticket.payload.request_id
            or receipt.run_id != ticket.payload.run_id
            or receipt.generation != ticket.payload.generation
        ):
            raise CommunicationClientError(
                "communication_client.delivery_receipt.binding_mismatch",
                ambiguous=True,
            )
        return receipt


__all__ = ["CommunicationClientError", "CommunicationControlClient"]
