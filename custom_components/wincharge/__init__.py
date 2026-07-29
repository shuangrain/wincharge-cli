"""WinCharge Home Assistant Custom Integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .wincharge_cli import WinChargeClient

DOMAIN = "wincharge"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """從 GUI 設定流程載入 WinCharge 整合。"""
    hass.data.setdefault(DOMAIN, {})

    config = entry.data
    api_key = config["api_key"]
    api_token = config["api_token"]
    api_uid = config["api_uid"]

    client = await hass.async_add_executor_job(WinChargeClient, api_key, api_token, api_uid)

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "config": config,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """解除載入 WinCharge 整合。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
