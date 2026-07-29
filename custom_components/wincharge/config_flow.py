"""Home Assistant UI 設定流程 (Config Flow)"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

DOMAIN = "wincharge"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("api_key"): str,
        vol.Required("api_token"): str,
        vol.Required("api_uid"): str,
        vol.Required("payment_password"): str,
        vol.Optional("charger_id", default="wincharge_ocppv16_SAMPLE123"): str,
    }
)


class WinChargeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """處理 GUI 設定精靈。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """處理使用者輸入介面。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title=f"WinCharge ({user_input['charger_id']})",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
