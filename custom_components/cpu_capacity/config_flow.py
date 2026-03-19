# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_PUBLISH_INTERVAL_SECONDS,
    CONF_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PUBLISH_INTERVAL_SECONDS,
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DOMAIN,
    UNIQUE_ID,
)


def _build_schema(sample_default: float, publish_default: float) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SAMPLE_INTERVAL_SECONDS,
                default=float(sample_default),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10.0)),
            vol.Required(
                CONF_PUBLISH_INTERVAL_SECONDS,
                default=float(publish_default),
            ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=3600.0)),
        }
    )


class CpuCapacityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CpuCapacityOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            sample_interval = float(user_input[CONF_SAMPLE_INTERVAL_SECONDS])
            publish_interval = float(user_input[CONF_PUBLISH_INTERVAL_SECONDS])
            if publish_interval < sample_interval:
                errors["base"] = "publish_too_small"
            else:
                await self.async_set_unique_id(UNIQUE_ID)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(
                DEFAULT_SAMPLE_INTERVAL_SECONDS,
                DEFAULT_PUBLISH_INTERVAL_SECONDS,
            ),
            errors=errors,
        )


class CpuCapacityOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            sample_interval = float(user_input[CONF_SAMPLE_INTERVAL_SECONDS])
            publish_interval = float(user_input[CONF_PUBLISH_INTERVAL_SECONDS])
            if publish_interval < sample_interval:
                errors["base"] = "publish_too_small"
            else:
                return self.async_create_entry(title="", data=user_input)

        sample_default = float(
            self._config_entry.options.get(
                CONF_SAMPLE_INTERVAL_SECONDS,
                self._config_entry.data.get(
                    CONF_SAMPLE_INTERVAL_SECONDS,
                    DEFAULT_SAMPLE_INTERVAL_SECONDS,
                ),
            )
        )
        publish_default = float(
            self._config_entry.options.get(
                CONF_PUBLISH_INTERVAL_SECONDS,
                self._config_entry.data.get(
                    CONF_PUBLISH_INTERVAL_SECONDS,
                    DEFAULT_PUBLISH_INTERVAL_SECONDS,
                ),
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(sample_default, publish_default),
            errors=errors,
        )
