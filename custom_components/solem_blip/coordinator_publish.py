"""Safe coordinator data publish helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator


def publish_descriptor_update(
    coordinator: SolemCoordinator, data: list[dict[str, Any]]
) -> None:
    """Push new entity descriptors without resetting the status poll timer."""
    coordinator.data = data
    coordinator.async_update_listeners()
