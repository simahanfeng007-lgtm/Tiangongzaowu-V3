"""Shared host-security primitives used by isolated runtime components."""

from .dpapi import DataProtector, EphemeralTestProtector, WindowsDpapiProtector, ephemeral_test_protector_for_scope
from .archive import ArchiveInspection, ArchiveInspectionError, ArchiveLimits, inspect_archive
from .ticket_verification import (
    TicketVerificationError,
    b64url_decode,
    b64url_encode,
    ticket_signing_input,
    verify_delivery_ticket,
    verify_execution_ticket,
    verify_omni_capability_grant,
    verify_service_auth_signature,
)

__all__ = [
    "ArchiveInspection",
    "ArchiveInspectionError",
    "ArchiveLimits",
    "DataProtector",
    "EphemeralTestProtector",
    "ephemeral_test_protector_for_scope",
    "TicketVerificationError",
    "WindowsDpapiProtector",
    "b64url_decode",
    "b64url_encode",
    "inspect_archive",
    "ticket_signing_input",
    "verify_delivery_ticket",
    "verify_execution_ticket",
    "verify_omni_capability_grant",
    "verify_service_auth_signature",
]
