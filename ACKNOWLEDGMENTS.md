# Acknowledgments

This project is a separate, minimal Home Assistant integration for the Solem BL-IP. It is not a successor to the full scheduler integration.

## Credits

- **[Henrique Craveiro (@hcraveiro)](https://github.com/hcraveiro)** — original [Home Assistant Solem Bluetooth Watering Controller](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller) integration and [Solem Schedule Card](https://github.com/hcraveiro/solem-schedule-card); early Home Assistant entity and coordinator patterns this project evolved from. If you want built-in scheduling, rain math, and the schedule card, use Henrique's integration.

- **[beelzetron/Home-Assistant-Solem-Bluetooth-Watering-Controller](https://github.com/beelzetron/Home-Assistant-Solem-Bluetooth-Watering-Controller)** — BL-IP BLE work, rename to Solem BL-IP, and PyPI library split on the beelzetron fork.

- **[pcman75/solem-blip-reverse-engineering](https://github.com/pcman75/solem-blip-reverse-engineering)** — BLE command protocol reference.

- **[solem-blip-ble](https://pypi.org/project/solem-blip-ble/)** — shared Python BLE client ([source](https://github.com/beelzetron/solem-blip-ble)).

## Brand icon

The integration device icon is a generic irrigation glyph from [Material Design Icons](https://pictogrammers.com/library/mdi/icon/sprinkler/) (Apache 2.0), not an official SOLEM logo. See [`custom_components/solem_blip/brand/README.md`](custom_components/solem_blip/brand/README.md).
