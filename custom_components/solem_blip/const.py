"""Constants for the Solem BL-IP integration."""

DOMAIN = "solem_blip"
V5_SERVICE_UUID = "108b0001-eab5-bc09-d0ea-0b8f467ce8ee"

DEFAULT_SCAN_INTERVAL = 120
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 3600

CONTROLLER_MAC_ADDRESS = "controller_mac_address"
NUM_STATIONS = "num_stations"
MIN_NUM_STATIONS = 1
MAX_NUM_STATIONS = 8

BLUETOOTH_TIMEOUT = "bluetooth_timeout"
BLUETOOTH_MIN_TIMEOUT = 5
BLUETOOTH_DEFAULT_TIMEOUT = 30
BLUETOOTH_MAX_TIMEOUT = 300

CONFIG_FLOW_BLUETOOTH_TIMEOUT = 60
CONFIG_FLOW_CONNECT_RETRIES = 3
CONFIG_FLOW_CONNECT_RETRY_DELAY = 5

METADATA_READ_TIMEOUT = 15
STATION_NAMES_READ_TIMEOUT = 35
METADATA_RETRY_INTERVAL = 15 * 60

IRRIGATION_CONFIG_READ_TIMEOUT = 30
IRRIGATION_CONFIG_RETRY_INTERVAL = 15 * 60
IRRIGATION_CONFIG_REFRESH_INTERVAL = 60 * 60
IRRIGATION_CONFIG_UPDATE_INTERVAL = 15 * 60

PROGRAM_LABELS = ("A", "B", "C")

SET_TIME_MIN_INTERVAL = 24 * 60 * 60

# Defer heavy GATT reads until status polling has been stable for this long.
HEAVY_READ_DEFER_SECONDS = 60

SOLEM_API_MOCK = "solem_api_mock"
PERSISTENT_CONNECTION = "persistent_connection"
# Hold the BLE link open indefinitely while persistent mode is enabled
# (no idle release). Maximizes connection stability on churn-sensitive
# controllers; the phone app cannot connect while the link is held.
PERSISTENT_HOLD_LINK = "persistent_hold_link"
# While the persistent client holds the BLE link between polls, hand the radio
# back at 75% of the scan interval so an overlapping poll never stalls.
PERSISTENT_IDLE_RELEASE_FRACTION = 0.75
# Upper bound for the bounded disconnect during coordinator shutdown.
PERSISTENT_DISCONNECT_TIMEOUT = 5
DEFAULT_MANUAL_DURATION = 10
DEFAULT_CONTROLLER_OFF_DAYS = 1
MAX_CONTROLLER_OFF_DAYS = 15

# Early-warning threshold on the reported battery icon level (0-5).
# The protocol low-battery alert only fires below raw voltage 50, which
# is inside the level-1 range (50-59): a controller can reach 1/5 and
# then die without the protocol alert ever triggering (#92).
BATTERY_EARLY_WARNING_LEVEL = 1
