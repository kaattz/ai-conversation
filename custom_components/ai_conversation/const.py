"""Constants for the integration."""

import logging
import voluptuous as vol  # noqa
from typing import Any, Optional, Callable  # noqa
from homeassistant.core import HomeAssistant, callback  # noqa
from homeassistant.const import (  # noqa
    Platform,
    CONF_NAME, CONF_BASE, CONF_API_KEY, CONF_SERVICE,
    CONF_MODEL, CONF_LLM_HASS_API, MATCH_ALL,
    ATTR_ENTITY_ID,
)
from homeassistant.util import slugify  # noqa
from homeassistant.exceptions import HomeAssistantError  # noqa
from homeassistant.config_entries import ConfigEntry, ConfigSubentry  # noqa

DOMAIN = "ai_conversation"
LOGGER = logging.getLogger(__package__)

MAX_TOOL_ITERATIONS = 10
CONF_CUSTOM = "custom"
CONF_PROMPT = "prompt"

PLATFORMS = (
    Platform.CONVERSATION,
)

GLM_BOX_START = "<|begin_of_box|>"
GLM_BOX_END = "<|end_of_box|>"


class AIConversationAPIError(HomeAssistantError):
    """Raised when the upstream AI provider returns a handled API error."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        payload: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = code
        self.status = status
        self.payload = payload or {}
