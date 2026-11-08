"""Operating-system backends for creating and removing owned virtual monitors."""

from .sudovda import (
    DriverError,
    DriverProtocolError,
    DriverUnavailableError,
    NativeIdentity,
    SudoVDADriver,
)

__all__ = [
    "DriverError",
    "DriverProtocolError",
    "DriverUnavailableError",
    "NativeIdentity",
    "SudoVDADriver",
]
