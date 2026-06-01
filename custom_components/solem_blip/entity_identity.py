"""Stable entity identity metadata for unique_id generation and migration."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .const import PROGRAM_LABELS
from .entity_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
    BUTTON_DESCRIPTIONS,
    NUMBER_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
)
from .util import format_entity_unique_id, format_legacy_unique_id, mac_to_uuid


@dataclass(frozen=True, slots=True)
class EntityIdentity:
    """One registry entity slot for a controller MAC and station count."""

    mac: str
    device_id: str
    device_uid: str
    platform: str
    legacy_suffix: str
    device_type: str

    @property
    def legacy_unique_id(self) -> str:
        """Counter-based unique_id used before config entry version 2."""
        return format_legacy_unique_id(self.mac, self.device_uid, self.legacy_suffix)

    @property
    def unique_id(self) -> str:
        """Stable unique_id derived from device_id."""
        return format_entity_unique_id(self.mac, self.device_id)


def _platform_for_device_type(device_type: str) -> str:
    if device_type in SENSOR_DESCRIPTIONS:
        return "sensor"
    if device_type in BINARY_SENSOR_DESCRIPTIONS:
        return "binary_sensor"
    if device_type in BUTTON_DESCRIPTIONS:
        return "button"
    if device_type in NUMBER_DESCRIPTIONS:
        return "number"
    msg = f"Unknown device_type: {device_type}"
    raise ValueError(msg)


def _legacy_suffix(device_type: str) -> str:
    if device_type in BUTTON_DESCRIPTIONS:
        return BUTTON_DESCRIPTIONS[device_type].key
    if device_type in NUMBER_DESCRIPTIONS:
        return NUMBER_DESCRIPTIONS[device_type].state_field
    return "state"


def _yield_identity(
    mac: str,
    device_id: str,
    device_type: str,
    device_uid: str,
) -> EntityIdentity:
    return EntityIdentity(
        mac=mac,
        device_id=device_id,
        device_uid=device_uid,
        platform=_platform_for_device_type(device_type),
        legacy_suffix=_legacy_suffix(device_type),
        device_type=device_type,
    )


def iter_entity_identities(mac: str, num_stations: int) -> Iterator[EntityIdentity]:
    """Yield every entity identity in the same order as descriptor builders."""
    counter = 1

    yield _yield_identity(
        mac,
        f"{mac}_irrigation_controller_status",
        "STATE_SENSOR",
        mac_to_uuid(mac, counter),
    )
    counter += 1

    yield _yield_identity(
        mac,
        f"{mac}_battery",
        "BATTERY_SENSOR",
        mac_to_uuid(mac, counter),
    )
    counter += 1

    yield _yield_identity(
        mac,
        f"{mac}_battery_voltage",
        "BATTERY_VOLTAGE_SENSOR",
        mac_to_uuid(mac, counter),
    )
    counter += 1

    yield _yield_identity(
        mac,
        f"{mac}_battery_low",
        "BATTERY_LOW_SENSOR",
        mac_to_uuid(mac, counter),
    )
    counter += 1

    yield _yield_identity(
        mac,
        f"{mac}_last_time_sync",
        "LAST_TIME_SYNC_SENSOR",
        mac_to_uuid(mac, counter),
    )
    counter += 1

    stations_counter = 801
    for station_id in range(1, num_stations + 1):
        yield _yield_identity(
            mac,
            f"{mac}_irrigation_station_{station_id}_status",
            "STATE_SENSOR",
            mac_to_uuid(mac, stations_counter),
        )
        stations_counter += 1

    remaining_counter = 601
    for station_id in range(1, num_stations + 1):
        yield _yield_identity(
            mac,
            f"{mac}_remaining_sprinkle_station_{station_id}",
            "REMAINING_SPRINKLE_SENSOR",
            mac_to_uuid(mac, remaining_counter),
        )
        remaining_counter += 1

    yield _yield_identity(
        mac,
        f"{mac}_irrigation_manual_duration",
        "IRRIGATION_DURATION_NUMBER",
        mac_to_uuid(mac, counter),
    )
    counter += 1

    buttons_counter = 901
    for station_id in range(1, num_stations + 1):
        yield _yield_identity(
            mac,
            f"{mac}_irrigation_manual_start_station_{station_id}",
            "SPRINKLE_BUTTON",
            mac_to_uuid(mac, buttons_counter),
        )
        buttons_counter += 1

    for device_type, device_suffix in (
        ("STOP_BUTTON", "irrigation_stop"),
        ("ON_BUTTON", "irrigation_controller_on"),
        ("OFF_BUTTON", "irrigation_controller_off"),
    ):
        yield _yield_identity(
            mac,
            f"{mac}_{device_suffix}",
            device_type,
            mac_to_uuid(mac, counter),
        )
        counter += 1

    program_counter = 1001
    for label in PROGRAM_LABELS:
        label_lower = label.lower()
        program_counter += 1  # reserved slot for removed program name sensor
        for device_type, device_suffix in (
            ("PROGRAM_NEXT_START_SENSOR", f"program_{label_lower}_next_start"),
            ("PROGRAM_RUNNING_SENSOR", f"program_{label_lower}_running"),
            ("PROGRAM_SCHEDULE_SENSOR", f"program_{label_lower}_schedule"),
        ):
            yield _yield_identity(
                mac,
                f"{mac}_{device_suffix}",
                device_type,
                mac_to_uuid(mac, program_counter),
            )
            program_counter += 1


def build_legacy_unique_id_map(mac: str, num_stations: int) -> dict[str, str]:
    """Map legacy unique_id values to stable unique_id values."""
    mapping: dict[str, str] = {}
    for identity in iter_entity_identities(mac, num_stations):
        mapping[identity.legacy_unique_id] = identity.unique_id
    return mapping
