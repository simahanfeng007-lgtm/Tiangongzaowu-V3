"""Channel adapters controlled by the Tiangong v3 total gateway."""

COMPONENT_ID = "tiangong-communication-service"
DEFAULT_PORT = 7176

API_CONTRACT = "tiangong.communication.api.v1"

from .delivery_dispatcher import (
    DeliveryChannelHandler,
    DeliveryDispatchError,
    VerifiedDeliveryDispatcher,
)
from .channel_authority import ChannelAuthorityError, ChannelAuthorityGate
from .drain import ChannelDrainNotReady, CommunicationDrainInspector
from .production_ingress import (
    CommunicationProductionIngress,
    ProductionIngressError,
)

__all__ = [
    "API_CONTRACT",
    "ChannelAuthorityError",
    "ChannelAuthorityGate",
    "ChannelDrainNotReady",
    "CommunicationDrainInspector",
    "CommunicationProductionIngress",
    "COMPONENT_ID",
    "DEFAULT_PORT",
    "DeliveryChannelHandler",
    "DeliveryDispatchError",
    "ProductionIngressError",
    "VerifiedDeliveryDispatcher",
]
