"""Coordinator polling and update tests."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.api import APIConnectionError
from custom_components.solem_blip.ble_health import note_cycle_outcome
from custom_components.solem_blip.const import (
    SOLEM_API_MOCK,
    TIME_SYNC_RETRY_INTERVAL,
)
from custom_components.solem_blip.coordinator import SolemCoordinator


@pytest.mark.asyncio
async def test_async_init_does_not_block_on_ble_io(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Entity descriptors are built before the first BLE poll."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()

    mock_solem_client.connect.assert_not_awaited()
    mock_solem_client.get_status.assert_not_awaited()
    assert coordinator.data
    assert coordinator.last_update_success is False
    assert coordinator.controller.state is None
    assert all(station.state is None for station in coordinator.stations)
    assert coordinator.battery_low is None
    assert coordinator._remaining_minutes_for_station(1) is None


@pytest.mark.asyncio
async def test_remaining_time_sensor_reports_minutes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Remaining-time descriptors expose rounded-up minutes, not raw seconds."""
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator._has_status = True
        coordinator.active_station_num = 1
        coordinator.remaining_seconds = 61

        data = await coordinator.async_update_all_sensors(fetch_status=False)

    station_1_remaining = next(
        descriptor
        for descriptor in data
        if descriptor["device_id"].endswith("_remaining_sprinkle_station_1")
    )
    station_2_remaining = next(
        descriptor
        for descriptor in data
        if descriptor["device_id"].endswith("_remaining_sprinkle_station_2")
    )
    assert station_1_remaining["state"] == 2
    assert station_2_remaining["state"] == 0


@pytest.mark.asyncio
async def test_async_update_data_raises_update_failed_on_ble_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """BLE poll errors mark coordinator updates as failed."""
    mock_solem_client.get_status.side_effect = APIConnectionError("Offline")

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()

        with pytest.raises(UpdateFailed, match="Offline"):
            await coordinator.async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_success_poll_has_no_teardown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A successful poll completes with no explicit release step.

    The v2 client is stateless (connect-per-operation): the integration never
    drives connection lifecycle, so there is nothing to release after a poll.
    """
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator.async_update_data()
    mock_solem_client.get_status.assert_awaited()


@pytest.mark.asyncio
async def test_async_update_data_failed_poll_is_wrapped(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A failing poll raises UpdateFailed; teardown is the client's own close."""
    mock_solem_client.get_status.side_effect = APIConnectionError("Offline")
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        with pytest.raises(UpdateFailed, match="Offline"):
            await coordinator.async_update_data()
    mock_solem_client.get_status.assert_awaited()


@pytest.mark.asyncio
class TestEntitySetupMetadata:
    """Metadata polling behavior."""

    async def test_device_metadata_is_surfaced(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Firmware and controller-provided station names are surfaced."""
        from homeassistant.helpers import device_registry as dr

        from custom_components.solem_blip.const import DOMAIN

        with patch(
            "custom_components.solem_blip.client_factory.StatelessSolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            mock_config_entry.add_to_hass(hass)
            device_registry = dr.async_get(hass)
            device = device_registry.async_get_or_create(
                config_entry_id=mock_config_entry.entry_id,
                identifiers={(DOMAIN, coordinator.controller_mac_address)},
            )

            await coordinator._fetch_device_status()
            await coordinator._fetch_device_metadata()
            data = await coordinator.async_update_all_sensors(fetch_status=False)

            assert coordinator.firmware_version == "5.1.5"
            assert coordinator.controller.software_version == "5.1.5"
            assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}
            assert any(d["device_name"] == "Zone 1 Status" for d in data)
            assert any(d["device_name"] == "Zone 1 remaining time" for d in data)
            assert any(d["device_name"] == "Sprinkle Zone 1" for d in data)
            assert device_registry.async_get(device.id).sw_version == "5.1.5"

    async def test_device_metadata_failures_are_cooled_down(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Optional metadata failures do not stall every status refresh."""
        import asyncio

        mock_solem_client.get_firmware_version.side_effect = asyncio.TimeoutError
        mock_solem_client.get_station_names.side_effect = asyncio.TimeoutError

        with patch(
            "custom_components.solem_blip.client_factory.StatelessSolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            await coordinator._fetch_device_metadata()
            await coordinator._fetch_device_metadata()

            mock_solem_client.get_firmware_version.assert_awaited_once()
            mock_solem_client.get_station_names.assert_awaited_once()
            assert caplog.messages[-1] == (
                "AA:BB:CC:DD:EE:FF - BLE cycle degraded "
                "(firmware read and station names read). "
                "The link usually recovers on the next poll. If the problem "
                "persists, check the controller's battery and radio range; "
                "rebooting a Bluetooth proxy, or restarting Home Assistant "
                "(which reloads the Bluetooth adapter), typically resolves it."
            )
            assert all(
                record.levelno == logging.DEBUG
                for record in caplog.records
                if "Failed to read" in record.getMessage()
            )

    async def test_metadata_timeout_does_not_prevent_later_status_poll(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Optional metadata timeout does not block the next battery poll."""
        import asyncio

        mock_solem_client.get_firmware_version.side_effect = asyncio.TimeoutError

        with patch(
            "custom_components.solem_blip.client_factory.StatelessSolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            await coordinator._fetch_device_metadata()
            await coordinator.async_update_data()

        mock_solem_client.get_status.assert_awaited_once()
        assert coordinator.battery_voltage == 90

    async def test_station_name_failure_cools_down_without_blocking_battery(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Station-name retries cool down while status polls keep advancing."""
        import asyncio

        mock_solem_client.get_station_names.side_effect = asyncio.TimeoutError

        with patch(
            "custom_components.solem_blip.client_factory.StatelessSolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            await coordinator._fetch_device_metadata()
            await coordinator.async_update_data()
            await coordinator._fetch_device_metadata()
            await coordinator.async_update_data()

        mock_solem_client.get_station_names.assert_awaited_once()
        assert mock_solem_client.get_status.await_count == 2
        assert coordinator.battery_voltage == 90


@pytest.mark.asyncio
async def test_set_time_runs_on_first_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Device time sync runs on the first real BLE poll when not mocked."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

    mock_solem_client.set_time.assert_awaited_once()
    assert coordinator._last_set_time_sync is not None
    assert coordinator._set_time_pending is False


@pytest.mark.asyncio
async def test_set_time_throttled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Device time sync is throttled after the first successful sync."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()
        await coordinator._fetch_device_status()

    mock_solem_client.set_time.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_time_retriggers_after_cycle_recovery(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A degraded-cycle recovery bypasses the 24h device-time throttle."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()
        # After the first sync the 24h throttle applies: no more set_time.
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 1

        # Degraded cycle followed by recovery forces a re-sync next poll.
        note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")
        note_cycle_outcome(coordinator, degraded=False, reason="")
        await coordinator._fetch_device_status()

    assert mock_solem_client.set_time.await_count == 2
    assert coordinator._set_time_pending is False


def _make_sync_test_entry(mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """Build the un-mocked-API config entry used by time-sync tests."""
    return MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )


@pytest.mark.asyncio
async def test_set_time_time_alarm_bypasses_throttle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A set clock alarm re-triggers time sync even within the 24h throttle."""
    config_entry = _make_sync_test_entry(mock_config_entry)
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 1

        # Alarm observed on the next poll, well inside the 24h throttle:
        # the sync must still run.
        coordinator.time_alarm = True
        await coordinator._fetch_device_status()

    assert mock_solem_client.set_time.await_count == 2


@pytest.mark.asyncio
async def test_set_time_alarm_success_clears_alarm_and_throttles(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """An alarm-triggered sync clears the alarm and does not re-trigger immediately."""
    config_entry = _make_sync_test_entry(mock_config_entry)
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

        coordinator.time_alarm = True
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 2
        # Verified success: the alarm cleared and the throttle was updated.
        assert coordinator.time_alarm is False

        # The device may take a poll or two to confirm; the throttle must
        # still suppress an immediate re-sync.
        await coordinator._fetch_device_status()

    assert mock_solem_client.set_time.await_count == 2


@pytest.mark.asyncio
@patch("custom_components.solem_blip.client_factory.StatelessSolemClient")
async def test_set_time_alarm_persists_cooldown_gates_retry(
    mock_client_cls: MagicMock,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A persistent clock alarm does not re-sync within the retry cooldown."""
    config_entry = _make_sync_test_entry(mock_config_entry)
    mock_client_cls.return_value = mock_solem_client
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        # The fixture status dict carries the clock alarm set: apply_status
        # re-sets time_alarm=True from the device on every poll.
        mock_solem_client.get_status.return_value = {
            **mock_solem_client.get_status.return_value,
            "time_alarm": True,
        }
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 1
        assert coordinator.time_alarm is True

        # The alarm-triggered sync fails (controller busy, as during a long
        # outage): the cooldown arms so the device is not hammered on every
        # poll.
        class _BusyStub(Exception):
            pass

        _BusyStub.__name__ = "SolemTimeSyncBusy"
        mock_solem_client.set_time.side_effect = _BusyStub("watering")
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 2
        assert coordinator.time_alarm is True
        assert (
            coordinator._time_sync_retry_after
            >= asyncio.get_running_loop().time() + TIME_SYNC_RETRY_INTERVAL - 1
        )

        # The alarm persists, but the second consecutive attempt is
        # cooldown-gated: no call within the cooldown window.
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 2
        assert coordinator.time_alarm is True


@pytest.mark.asyncio
@patch("custom_components.solem_blip.client_factory.StatelessSolemClient")
async def test_set_time_alarm_persists_after_success_cooldown_gates_resync(
    mock_client_cls: MagicMock,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Re-armed alarm from the next status read does not instantly re-sync.

    After a verified sync the alarm is cleared optimistically; when the next
    status read re-sets time_alarm=True from the device, it is the retry
    cooldown (armed by the following failed attempt) — not the optimistic
    clear — that prevents an immediate re-sync.
    """
    config_entry = _make_sync_test_entry(mock_config_entry)
    mock_client_cls.return_value = mock_solem_client
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 1
        assert coordinator.time_alarm is False

        # Next status read re-sets the alarm from the device. maybe_set_device_time
        # runs before get_status, so this poll only observes the alarm; the
        # alarm-triggered attempt happens on the following poll.
        mock_solem_client.get_status.side_effect = None
        mock_solem_client.get_status.return_value = {
            **mock_solem_client.get_status.return_value,
            "time_alarm": True,
        }
        await coordinator._fetch_device_status()
        assert coordinator.time_alarm is True
        assert mock_solem_client.set_time.await_count == 1

        # The alarm-triggered attempt fails: the cooldown arms, the 24h
        # throttle stays untouched.
        class _Stub(Exception):
            pass

        _Stub.__name__ = "SolemTimeSyncVerificationFailed"
        mock_solem_client.set_time.side_effect = _Stub("alarm still set")
        await coordinator._fetch_device_status()
        assert mock_solem_client.set_time.await_count == 2
        assert coordinator._time_sync_retry_after > 0.0

        # The cooldown — not the optimistic clear — suppresses the immediate
        # re-sync while the device keeps reporting the alarm.
        await coordinator._fetch_device_status()
        assert coordinator.time_alarm is True
        assert mock_solem_client.set_time.await_count == 2

        # Once the cooldown expires, the persisted alarm re-allows the sync:
        # pinning that the cooldown, not the optimistic clear, was the gate.
        coordinator._time_sync_retry_after = 0.0
        await coordinator._fetch_device_status()

    assert mock_solem_client.set_time.await_count == 3


@pytest.mark.asyncio
async def test_set_time_time_alarm_none_keeps_existing_behavior(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """time_alarm=None (never seen) keeps the pre-alarm trigger behavior."""
    config_entry = _make_sync_test_entry(mock_config_entry)
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()
        await coordinator._fetch_device_status()

    mock_solem_client.set_time.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_class_name", "expected_level", "throttle_updated"),
    [
        ("SolemTimeSyncBusy", logging.DEBUG, False),
        ("SolemTimeSyncRejected", logging.WARNING, False),
        ("SolemTimeSyncVerificationFailed", logging.WARNING, False),
        ("", logging.DEBUG, True),
    ],
)
async def test_set_time_outcome_classes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
    exc_class_name: str,
    expected_level: int,
    throttle_updated: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Time-sync outcomes are logged distinctly and defer or sync correctly.

    A deferred sync (Busy) or a failed one must not update the throttle, so
    the sync can retry on the next poll; a successful sync updates it. The
    empty class name is the success row. Classification is by exception class
    name because the pinned solem-blip-ble 0.3.1 does not export the classes
    yet.
    """

    class _Stub(Exception):
        pass

    _Stub.__name__ = exc_class_name

    config_entry = _make_sync_test_entry(mock_config_entry)
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ), caplog.at_level(logging.DEBUG):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        mock_solem_client.set_time.reset_mock()
        if exc_class_name:
            mock_solem_client.set_time.side_effect = _Stub("controller says no")
        coordinator._set_time_pending = False
        coordinator._last_set_time_at = (
            asyncio.get_running_loop().time() - 24 * 60 * 60
        )
        await coordinator._fetch_device_status()

        matching = [
            record
            for record in caplog.records
            if record.name == "custom_components.solem_blip.coordinator_polling"
            and "time" in record.getMessage().lower()
        ]
        if exc_class_name:
            # Failure rows log exactly one failure record at their level.
            assert matching
            assert all(record.levelno == expected_level for record in matching)
        else:
            # Success row: the only "time" log is the DEBUG synced record.
            assert matching
            assert all(
                record.levelno == logging.DEBUG and "synced" in record.getMessage()
                for record in matching
            )

        # Success: the throttle is updated so the 24h gate applies.
        if throttle_updated:
            assert coordinator._last_set_time_at >= (
                asyncio.get_running_loop().time() - 24 * 60 * 60
            )
        # Throttle untouched: the next poll retries the sync.
        if not throttle_updated:
            assert coordinator._last_set_time_at < (
                asyncio.get_running_loop().time() - 24 * 60 * 60
            )
        mock_solem_client.set_time.reset_mock(side_effect=True)
        mock_solem_client.set_time.side_effect = None
        await coordinator._fetch_device_status()

    if throttle_updated:
        # Success row: the 24h throttle now applies, so no retry call.
        assert mock_solem_client.set_time.await_count == 0
    else:
        assert mock_solem_client.set_time.await_count == 1


@pytest.mark.asyncio
async def test_irrigation_config_failures_are_cooled_down(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Irrigation config read failures apply a retry cooldown."""
    import asyncio

    mock_solem_client.get_irrigation_config.side_effect = asyncio.TimeoutError

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator.schedule_coordinator.async_refresh()
        await coordinator.schedule_coordinator.async_refresh()

    mock_solem_client.get_irrigation_config.assert_awaited_once()
    assert coordinator.irrigation_programs == {}


@pytest.mark.asyncio
async def test_status_poll_does_not_read_irrigation_config(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Slow schedule reads do not delay the status coordinator."""
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

    mock_solem_client.get_irrigation_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_irrigation_config_read_is_deferred_while_watering(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Schedule coordinator skips BLE reads during manual irrigation."""
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator._irrigation_active = True
        await coordinator.schedule_coordinator.async_refresh()

    mock_solem_client.get_irrigation_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_poll_runs_during_manual_irrigation(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Device-initiated program runs still receive status polls during HA manual irrigation."""
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator._irrigation_active = True
        await coordinator._fetch_device_status()

    mock_solem_client.get_status.assert_awaited_once()
    mock_solem_client.get_station_names.assert_not_awaited()


@pytest.mark.asyncio
async def test_station_names_slow_read_uses_extended_timeout(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Station name reads longer than 5s succeed within STATION_NAMES_READ_TIMEOUT."""
    import asyncio

    async def slow_names() -> dict[int, str]:
        await asyncio.sleep(0.01)
        return {1: "Zone 1", 2: "Zone 2"}

    mock_solem_client.get_station_names = slow_names

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}


@pytest.mark.asyncio
async def test_station_names_partial_reads_merge(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Partial station-name reads accumulate across retries."""
    responses = [{1: "Zone 1"}, {2: "Zone 2"}]

    async def partial_names() -> dict[int, str]:
        return responses.pop(0)

    mock_solem_client.get_station_names = partial_names

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_metadata()
        coordinator._station_names_retry_after = 0.0
        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}


@pytest.mark.asyncio
async def test_two_consecutive_status_polls(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Two status polls both call get_status and advance last poll timestamp."""
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        first_poll_at = None

        await coordinator.async_update_data()
        first_poll_at = coordinator._last_successful_poll_at
        assert first_poll_at is not None

        await coordinator.async_update_data()

    assert mock_solem_client.get_status.await_count == 2
    assert coordinator._last_successful_poll_at >= first_poll_at


@pytest.mark.asyncio
async def test_metadata_deferred_until_heavy_read_gate(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Metadata background task does not start until the defer gate elapses."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

        assert coordinator._metadata_task is None
        assert coordinator._first_successful_status_at is not None

        coordinator._metadata_ready_after = 0.0
        await coordinator._fetch_device_status()
        await hass.async_block_till_done()

    mock_solem_client.get_firmware_version.assert_awaited()
    mock_solem_client.get_station_names.assert_awaited()


@pytest.mark.asyncio
async def test_schedule_refresh_does_not_call_main_async_set_updated_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Schedule refresh publishes descriptors without resetting the main poll timer."""
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        main_set_updated = coordinator.async_set_updated_data
        coordinator.async_set_updated_data = MagicMock(wraps=main_set_updated)

        await coordinator.schedule_coordinator.async_refresh()

    coordinator.async_set_updated_data.assert_not_called()
    assert coordinator.irrigation_programs


@pytest.mark.asyncio
async def test_schedule_first_refresh_waits_for_heavy_read_gate(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Schedule coordinator defers its first irrigation config read."""
    import asyncio

    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator.schedule_coordinator.async_start_first_refresh()
        await asyncio.sleep(0.05)

        mock_solem_client.get_irrigation_config.assert_not_awaited()

        coordinator._schedule_ready_after = 0.0
        coordinator._schedule_gate.set()
        await hass.async_block_till_done()

    mock_solem_client.get_irrigation_config.assert_awaited()


@pytest.mark.asyncio
async def test_schedule_first_refresh_reads_metadata_before_config(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Bootstrap metadata completes before the first schedule read starts."""
    import asyncio

    calls: list[str] = []

    async def get_firmware_version() -> dict[str, object]:
        calls.append("firmware")
        return {"major": 5, "minor": 1, "patch": 7, "raw_hex": "5.1.7"}

    async def get_station_names() -> dict[int, str]:
        calls.append("station_names")
        return {1: "Zone 1", 2: "Zone 2"}

    async def get_irrigation_config() -> dict[int, object]:
        calls.append("irrigation_config")
        return {}

    mock_solem_client.get_firmware_version = AsyncMock(side_effect=get_firmware_version)
    mock_solem_client.get_station_names = AsyncMock(side_effect=get_station_names)
    mock_solem_client.get_irrigation_config = AsyncMock(
        side_effect=get_irrigation_config
    )
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator._schedule_ready_after = (
            asyncio.get_running_loop().time() + 0.05
        )
        coordinator._schedule_gate.set()
        coordinator.schedule_coordinator.async_start_first_refresh()
        await asyncio.sleep(0.2)
        await hass.async_block_till_done()

    assert calls == ["firmware", "station_names", "irrigation_config"]


@pytest.mark.asyncio
async def test_first_status_poll_opens_schedule_gate(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A successful status poll opens the schedule gate and arms the defer."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

        assert coordinator._first_successful_status_at is not None
        assert coordinator._schedule_gate.is_set()
        assert coordinator._schedule_ready_after != float("inf")


@pytest.mark.asyncio
async def test_schedule_first_refresh_cancelled_on_entry_shutdown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """The deferred first refresh is a background task cancelled on shutdown."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator.schedule_coordinator.async_start_first_refresh()
        await hass.async_block_till_done()

        assert mock_config_entry._background_tasks

        mock_config_entry.async_shutdown()
        await hass.async_block_till_done()

    mock_solem_client.get_firmware_version.assert_not_awaited()
    mock_solem_client.get_station_names.assert_not_awaited()
    mock_solem_client.get_irrigation_config.assert_not_awaited()
