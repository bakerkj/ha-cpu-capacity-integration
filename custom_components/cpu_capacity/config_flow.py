# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback

from .const import (
    CONF_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PUBLISH_INTERVAL_SECONDS,
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DOMAIN,
    MAX_PUBLISH_INTERVAL,
    MAX_SAMPLE_INTERVAL,
    MIN_PUBLISH_INTERVAL,
    MIN_SAMPLE_INTERVAL,
    PUBLISH_INTERVAL_CONF_BY_WINDOW,
    UNIQUE_ID,
    resolve_publish_interval,
)


def _build_schema(
    sample_default: float, publish_defaults: Mapping[str, float]
) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_SAMPLE_INTERVAL_SECONDS,
            default=float(sample_default),
        ): vol.All(
            vol.Coerce(float),
            vol.Range(min=MIN_SAMPLE_INTERVAL, max=MAX_SAMPLE_INTERVAL),
        ),
    }
    for window, conf in PUBLISH_INTERVAL_CONF_BY_WINDOW.items():
        schema[vol.Required(conf, default=float(publish_defaults[window]))] = vol.All(
            vol.Coerce(float),
            vol.Range(min=MIN_PUBLISH_INTERVAL, max=MAX_PUBLISH_INTERVAL),
        )
    return vol.Schema(schema)


def _publish_error(user_input: dict[str, Any]) -> str | None:
    """Each window must publish no faster than the sampler produces data."""
    sample_interval = float(user_input[CONF_SAMPLE_INTERVAL_SECONDS])
    for conf in PUBLISH_INTERVAL_CONF_BY_WINDOW.values():
        if float(user_input[conf]) < sample_interval:
            return "publish_too_small"
    return None


@config_entries.HANDLERS.register(DOMAIN)
class CpuCapacityConfigFlow(config_entries.ConfigFlow):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CpuCapacityOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            error = _publish_error(user_input)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(UNIQUE_ID)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(
                DEFAULT_SAMPLE_INTERVAL_SECONDS,
                {
                    window: DEFAULT_PUBLISH_INTERVAL_SECONDS
                    for window in PUBLISH_INTERVAL_CONF_BY_WINDOW
                },
            ),
            errors=errors,
        )


class CpuCapacityOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            error = _publish_error(user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="", data=user_input)

        source = {**self._config_entry.data, **self._config_entry.options}
        sample_default = float(
            source.get(CONF_SAMPLE_INTERVAL_SECONDS, DEFAULT_SAMPLE_INTERVAL_SECONDS)
        )
        # Pre-existing entries only have the legacy single value;
        # resolve_publish_interval seeds every window from it so the form
        # opens reflecting the current behaviour.
        publish_defaults = {
            window: resolve_publish_interval(window, source)
            for window in PUBLISH_INTERVAL_CONF_BY_WINDOW
        }

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(sample_default, publish_defaults),
            errors=errors,
        )
