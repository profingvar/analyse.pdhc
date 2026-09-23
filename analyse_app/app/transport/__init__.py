"""The coordinator <-> node wire (#684 / AN-12)."""
from .envelope import (
    REQUEST_KIND, RESPONSE_KIND, SealError, TransportError, UnsealError,
    seal, secret_from_config, unseal,
)

__all__ = ["seal", "unseal", "secret_from_config", "REQUEST_KIND",
           "RESPONSE_KIND", "TransportError", "SealError", "UnsealError"]
