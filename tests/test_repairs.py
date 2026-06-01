"""Repair issue tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.solem_blip.bluetooth_issue import (
    CONSECUTIVE_FAILURES_THRESHOLD,
    async_manage_bluetooth_issue,
)


@pytest.mark.asyncio
async def test_repair_issue_created_after_repeated_failures(coordinator) -> None:
    """Repair issue is created after consecutive coordinator update failures."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_delete_issue"
    ) as delete_issue:
        for _ in range(CONSECUTIVE_FAILURES_THRESHOLD):
            async_manage_bluetooth_issue(coordinator, success=False)

        create_issue.assert_called_once()
        delete_issue.assert_not_called()


@pytest.mark.asyncio
async def test_repair_issue_cleared_after_success(coordinator) -> None:
    """Repair issue is cleared after a successful coordinator update."""
    coordinator._consecutive_update_failures = CONSECUTIVE_FAILURES_THRESHOLD

    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_delete_issue"
    ) as delete_issue:
        async_manage_bluetooth_issue(coordinator, success=True)

    delete_issue.assert_called_once()
    create_issue.assert_not_called()
    assert coordinator._consecutive_update_failures == 0
