"""What a config entry holds while it is loaded: one coordinator per connection."""

from __future__ import annotations

from dataclasses import dataclass

from .cloud.coordinator import CloudCoordinator
from .coordinator import EcoflowCoordinator


@dataclass
class HybridData:
    """The coordinators of one system."""

    # None without a Modbus connection.
    modbus: EcoflowCoordinator | None
    # None without a cloud connection.
    cloud: CloudCoordinator | None = None
