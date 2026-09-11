"""The Asterisk Dongle integration."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    DATA_ASTERISK_MANAGER,
    DATA_DEVICES,
    DISCOVERY_INTERVAL,
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
)
from .manager import AsteriskManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.NOTIFY, Platform.SENSOR]


def _strip_ami_output_prefix(line: str) -> str:
    """Снимает префикс 'Output: ' / 'Output:' из строки ответа AMI."""
    return re.sub(r"^Output:\s?", "", line)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции из config entry."""
    hass.data.setdefault(DOMAIN, {})

    manager = AsteriskManager(
        host=entry.data["host"],
        port=entry.data.get("port", 5038),
        username=entry.data["username"],
        password=entry.data["password"],
    )

    if not await hass.async_add_executor_job(manager.test_connection):
        _LOGGER.error("Не удалось подключиться к Asterisk AMI")
        return False

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_ASTERISK_MANAGER: manager,
        DATA_DEVICES: {},
    }

    main_device = await _create_main_device(hass, entry)
    hass.data[DOMAIN][entry.entry_id]["main_device_id"] = main_device.id

    await _discover_devices(hass, entry)

    async def _periodic_discovery(now):
        await _discover_devices(hass, entry)

    entry.async_on_unload(
        async_track_time_interval(
            hass, _periodic_discovery, timedelta(seconds=DISCOVERY_INTERVAL)
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка интеграции."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and DATA_ASTERISK_MANAGER in data:
            manager = data[DATA_ASTERISK_MANAGER]
            await hass.async_add_executor_job(manager.disconnect)

    return unload_ok


async def _create_main_device(hass: HomeAssistant, entry: ConfigEntry):
    """Создает главное устройство для интеграции и возвращает его."""
    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Asterisk AMI ({entry.data['host']})",
        manufacturer="Asterisk",
        model="AMI Gateway",
        sw_version="1.0",
    )
    return device_entry


async def _discover_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Обнаружение донглов через AMI."""
    data = hass.data[DOMAIN][entry.entry_id]
    manager: AsteriskManager = data[DATA_ASTERISK_MANAGER]
    devices: dict[str, dict[str, Any]] = data[DATA_DEVICES]

    response = await hass.async_add_executor_job(
        manager.send_command, "dongle show devices"
    )
    if not response:
        _LOGGER.warning("Не удалось получить список донглов")
        return

    _LOGGER.debug("Ответ 'dongle show devices':\n%s", response)

    discovered = _parse_devices_response(response)
    _LOGGER.debug("Обнаружено устройств: %s", list(discovered.keys()))

    discovered_imeis = set(discovered.keys())
    current_imeis = set(devices.keys())

    # Новые устройства
    for imei in discovered_imeis - current_imeis:
        device_info = discovered[imei]
        devices[imei] = device_info
        await _register_device(hass, entry, device_info)
        async_dispatcher_send(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{entry.entry_id}", device_info
        )
        _LOGGER.info("Обнаружен новый донгл: %s", imei)

    # Обновление существующих
    for imei in discovered_imeis & current_imeis:
        devices[imei].update(discovered[imei])

    # Удалённые устройства
    for imei in current_imeis - discovered_imeis:
        device_info = devices.pop(imei)
        async_dispatcher_send(
            hass, f"{SIGNAL_DEVICE_REMOVED}_{entry.entry_id}", device_info
        )
        _LOGGER.info("Донгл удалён: %s", imei)


async def _register_device(
    hass: HomeAssistant, entry: ConfigEntry, device_info: dict[str, Any]
) -> None:
    """Регистрация устройства в реестре устройств HA."""
    device_registry = dr.async_get(hass)
    data = hass.data[DOMAIN][entry.entry_id]
    main_device_id = data.get("main_device_id")

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_info["imei"])},
        via_device_id=main_device_id,
        name=f"Dongle {device_info.get('number') or device_info['imei']}",
        manufacturer=device_info.get("model") or "GSM Dongle",
        model=device_info.get("model"),
        sw_version=device_info.get("firmware"),
    )


def _parse_devices_response(response: str) -> dict[str, dict[str, Any]]:
    """
    Парсит вывод команды 'dongle show devices'.

    Реальный ответ AMI (Keenetic-2918):
        Response: Success
        Message: Command output follows
        Output: ID           Group State      RSSI Mode Submode Provider Name  Model      Firmware          IMEI             IMSI             Number
        Output: dongle0      0     Free       21   0    0       beeline        E173       11.126.85.00.209  357291041830484  250997278767099  Unknown

    Поля в data-строке (после снятия 'Output: '):
        [0] dongle0              dongle_id
        [1] 0                    group
        [2] Free                 state
        [3] 21                   rssi_raw
        [4] 0                    mode
        [5] 0                    submode
        [6] beeline              provider
        [7] E173                 model
        [8] 11.126.85.00.209     firmware
        [9] 357291041830484      IMEI
        [10] 250997278767099     IMSI
        [11] Unknown             number
    """
    devices: dict[str, dict[str, Any]] = {}

    # Снимаем префикс 'Output: ' и убираем пустые/служебные строки
    normalized_lines = []
    for raw_line in response.splitlines():
        if raw_line.startswith("Response:") or raw_line.startswith("Message:"):
            continue
        line = _strip_ami_output_prefix(raw_line)
        normalized_lines.append(line)

    # Ищем строку заголовка
    header_idx = None
    for i, line in enumerate(normalized_lines):
        if "State" in line and "IMEI" in line and "Number" in line:
            header_idx = i
            break

    if header_idx is None:
        _LOGGER.warning(
            "Не найден заголовок в выводе 'dongle show devices'. Ответ:\n%s",
            response,
        )
        return devices

    for line in normalized_lines[header_idx + 1:]:
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        if line.startswith("--END COMMAND--"):
            continue

        parts = re.split(r"\s+", line)
        if len(parts) < 10:
            continue

        dongle_id = parts[0]
        group     = parts[1]  if len(parts) > 1  else ""
        state     = parts[2]  if len(parts) > 2  else ""
        rssi_raw  = parts[3]  if len(parts) > 3  else ""
        mode      = parts[4]  if len(parts) > 4  else ""
        submode   = parts[5]  if len(parts) > 5  else ""
        provider  = parts[6]  if len(parts) > 6  else ""
        model     = parts[7]  if len(parts) > 7  else ""
        firmware  = parts[8]  if len(parts) > 8  else ""
        imei      = parts[9]  if len(parts) > 9  else dongle_id
        imsi      = parts[10] if len(parts) > 10 else ""
        number    = parts[11] if len(parts) > 11 else ""

        # Защита: IMEI должен содержать только цифры и иметь длину 14-16
        if not (imei.isdigit() and 14 <= len(imei) <= 16):
            _LOGGER.warning(
                "Похоже, строка разобрана неверно (IMEI='%s'). Пропускаю: %s",
                imei, line,
            )
            continue

        devices[imei] = {
            "dongle_id": dongle_id,
            "group": group,
            "state": state,
            "rssi_raw": rssi_raw,
            "mode": mode,
            "submode": submode,
            "provider": provider,
            "model": model,
            "firmware": firmware,
            "imei": imei,
            "imsi": imsi,
            "number": number,
        }

    return devices