"""Константы для интеграции Asterisk Dongle."""
from typing import Final

DOMAIN: Final = "asterisk_dongle"

# Конфигурационные ключи
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Значения по умолчанию
DEFAULT_PORT: Final = 5038
DEFAULT_SCAN_INTERVAL: Final = 60
DISCOVERY_INTERVAL: Final = 300  # 5 минут вместо 1 часа для лучшего обнаружения

# Ключи для hass.data
DATA_ASTERISK_MANAGER: Final = "asterisk_manager"
DATA_DEVICES: Final = "devices"
DATA_CONFIG_ENTRY: Final = "config_entry"
DATA_SCAN_INTERVAL: Final = "scan_interval"   # <-- добавлено

# Уникальные идентификаторы
ATTR_IMEI: Final = "imei"
ATTR_DONGLE_ID: Final = "dongle_id"
ATTR_MODEL: Final = "model"

# Сигналы для обновления устройств
SIGNAL_DEVICE_DISCOVERED: Final = "asterisk_dongle_device_discovered"
SIGNAL_DEVICE_REMOVED: Final = "asterisk_dongle_device_removed"

# Типы платформ
PLATFORM_NOTIFY: Final = "notify"
PLATFORM_SENSOR: Final = "sensor"

# Типы сервисов notify
SERVICE_SMS: Final = "sms"
SERVICE_USSD: Final = "ussd"

# Ключи для данных сервиса
ATTR_NUMBER: Final = "number"
ATTR_MESSAGE: Final = "message"
ATTR_USSD_CODE: Final = "ussd_code"