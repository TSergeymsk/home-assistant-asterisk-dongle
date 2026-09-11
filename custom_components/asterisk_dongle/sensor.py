"""Support for Asterisk Dongle sensors."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    DATA_ASTERISK_MANAGER,
    DATA_DEVICES,
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка сенсоров для всех донглов."""
    data = hass.data[DOMAIN][entry.entry_id]
    manager = data[DATA_ASTERISK_MANAGER]
    devices = data[DATA_DEVICES]
    main_device_id = data.get("main_device_id")

    entities = []
    for imei, device_info in devices.items():
        sensor = AsteriskDongleSignalSensor(
            hass=hass,
            manager=manager,
            device_info=device_info,
            entry_id=entry.entry_id,
            main_device_id=main_device_id,
        )
        entities.append(sensor)

    if entities:
        async_add_entities(entities, update_before_add=True)

    @callback
    def async_add_sensor(device_info):
        new_sensor = AsteriskDongleSignalSensor(
            hass=hass,
            manager=manager,
            device_info=device_info,
            entry_id=entry.entry_id,
            main_device_id=main_device_id,
        )
        async_add_entities([new_sensor], update_before_add=True)

    @callback
    def async_remove_sensor(device_info):
        imei = device_info["imei"]
        entity_id = f"sensor.dongle_{imei}_cell_signal"
        if hass.states.get(entity_id):
            hass.async_create_task(
                hass.services.async_call(
                    "homeassistant", "remove_entity", {"entity_id": entity_id}
                )
            )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{entry.entry_id}",
            async_add_sensor,
        )
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_REMOVED}_{entry.entry_id}",
            async_remove_sensor,
        )
    )


class AsteriskDongleSignalSensor(SensorEntity):
    """Сенсор уровня сигнала GSM-донгла."""

    _attr_should_poll = True

    def __init__(
        self,
        hass: HomeAssistant,
        manager,
        device_info: dict[str, Any],
        entry_id: str,
        main_device_id: str | None = None,
    ):
        self.hass = hass
        self._manager = manager
        self._device_info = device_info
        self._entry_id = entry_id
        self._main_device_id = main_device_id

        imei = device_info["imei"]
        self._attr_unique_id = f"asterisk_dongle_{imei}_signal"
        self._attr_name = f"Cell Signal {imei}"
        self._attr_native_unit_of_measurement = "dBm"
        self._attr_icon = "mdi:signal"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._device_info["imei"])},
            "name": f"Dongle {self._device_info.get('number') or self._device_info['imei']}",
            "manufacturer": (
                self._device_info.get("manufacturer")
                or self._device_info.get("model")
                or "GSM Dongle"
            ),
            "model": self._device_info.get("model"),
            "sw_version": self._device_info.get("firmware"),
            "via_device_id": self._main_device_id,
        }

    async def async_update(self) -> None:
        """Обновление состояния сенсора."""
        dongle_id = self._device_info.get("dongle_id")
        if not dongle_id:
            _LOGGER.debug("Sensor %s: нет dongle_id", self._attr_unique_id)
            return

        response = await self.hass.async_add_executor_job(
            self._manager.send_command, f"dongle show device state {dongle_id}"
        )
        if not response:
            _LOGGER.debug(
                "Sensor %s: пустой ответ AMI для %s",
                self._attr_unique_id, dongle_id,
            )
            return

        _LOGGER.debug("Sensor %s: ответ AMI:\n%s", self._attr_unique_id, response)

        state = self._parse_dongle_state(response)
        if not state:
            _LOGGER.debug("Sensor %s: не удалось распарсить ответ", self._attr_unique_id)
            return

        # Обновляем доп. инфо об устройстве
        for dst, src in (
            ("provider", "provider_name"),
            ("model", "model"),
            ("firmware", "firmware"),
            ("number", "subscriber_number"),
            ("imsi", "imsi"),
            ("manufacturer", "manufacturer"),
        ):
            value = state.get(src)
            if value:
                self._device_info[dst] = value

        rssi = self._extract_signal_value(state.get("rssi", ""))
        _LOGGER.debug(
            "Sensor %s: rssi='%s', parsed=%s",
            self._attr_unique_id, state.get("rssi"), rssi,
        )

        if rssi is not None:
            self._attr_native_value = rssi

        self._attr_extra_state_attributes = {
            "raw_rssi": state.get("rssi"),
            "provider": state.get("provider_name"),
            "registration": state.get("gsm_registration_status"),
            "network_mode": state.get("mode"),
            "submode": state.get("submode"),
            "lac": state.get("location_area_code"),
            "cell_id": state.get("cell_id"),
            "signal_quality": self._calculate_signal_quality(rssi),
            "manufacturer": state.get("manufacturer"),
        }

    def _parse_dongle_state(self, response: str) -> dict[str, Any]:
        """
        Парсит вывод 'dongle show device state <id>'.

        Каждая строка данных идёт с префиксом 'Output: ' и содержит
        ещё один ':' внутри:
            Output:   Device                  : dongle0
            Output:   State                   : Free
            Output:   RSSI                    : 21, -71 dBm
        """
        state: dict[str, Any] = {}
        for raw_line in response.splitlines():
            # Снимаем префикс 'Output: '
            line = re.sub(r"^Output:\s?", "", raw_line)
            line = line.rstrip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            if key:
                state[key] = value
        return state

    def _extract_signal_value(self, raw: str) -> int | None:
        """Извлекает значение RSSI в dBm. Вход: '21, -71 dBm'."""
        if not raw:
            return None

        # Основной вариант: "-71 dBm"
        match = re.search(r"(-?\d+)\s*dBm", raw)
        if match:
            return int(match.group(1))

        # Резерв: сырое значение (0..31) → dBm
        match = re.search(r"(\d+)", raw)
        if match:
            raw_value = int(match.group(1))
            return (raw_value * 2) - 113

        return None

    def _calculate_signal_quality(self, rssi: int | None) -> str:
        if rssi is None:
            return "Unknown"
        if rssi >= -70:
            return "Excellent"
        if rssi >= -85:
            return "Good"
        if rssi >= -100:
            return "Fair"
        return "Poor"