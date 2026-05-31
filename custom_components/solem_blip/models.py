class IrrigationDevice:
    """Classe base para dispositivos de irrigação."""
    
    def __init__(self, device_id, device_name, device_uid, software_version, icon):
        self.device_id = device_id
        self.device_name = device_name
        self.device_uid = device_uid
        self.software_version = software_version
        self.state = None
        self.icon = icon
        self.last_reboot = None

    def update_state(self, new_state):
        """Atualiza o estado do dispositivo."""
        self.state = new_state


class IrrigationController(IrrigationDevice):
    """Classe para representar o controlador de irrigação."""
    
    def __init__(self, device_id, device_name, device_uid, software_version, icon):
        super().__init__(device_id, device_name, device_uid, software_version, icon)


class IrrigationStation(IrrigationDevice):
    """Classe para representar uma estação de irrigação."""
    
    def __init__(self, device_id, device_name, device_uid, station_number, software_version, icon):
        super().__init__(device_id, device_name, device_uid, software_version, icon)
        self.station_number = station_number
