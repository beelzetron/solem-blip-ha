"""Irrigation device models for the Solem BL-IP integration."""


class IrrigationDevice:
    """Base class for irrigation devices."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        device_uid: str,
        software_version: str | None,
    ) -> None:
        """Initialise irrigation device."""
        self.device_id = device_id
        self.device_name = device_name
        self.device_uid = device_uid
        self.software_version = software_version
        self.state: str | None = None
        self.last_reboot: str | None = None

    def update_state(self, new_state: str) -> None:
        """Update device state."""
        self.state = new_state


class IrrigationController(IrrigationDevice):
    """Irrigation controller status model."""


class IrrigationStation(IrrigationDevice):
    """Irrigation station status model."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        device_uid: str,
        station_number: int,
        software_version: str | None,
    ) -> None:
        """Initialise irrigation station."""
        super().__init__(device_id, device_name, device_uid, software_version)
        self.station_number = station_number
