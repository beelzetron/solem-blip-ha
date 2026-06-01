
def normalize_entity_state(value: str | None) -> str:
    """Normalize controller/station state strings for HA entity state."""
    if not value:
        return "unknown"
    return value.strip().lower().replace(" ", "_")


def mac_to_uuid(mac: str, last_part: int) -> str:
    mac_numbers = mac.replace(":", "")
    x_part = f"{mac_numbers[:4]}-{mac_numbers[4:8]}-{mac_numbers[8:12]}"
    yyy_part = f"{last_part:03d}"
    return f"{x_part}-{yyy_part}"
