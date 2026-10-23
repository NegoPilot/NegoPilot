"""HTTP settings without creating model or MCP clients."""

import math
import os
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ApiSettings:
    mode: Literal["live", "stub"] = "live"
    internal_api_key: str = field(default="", repr=False)
    request_timeout_seconds: float = 30.0
    mail_send_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.mode not in {"live", "stub"}:
            raise ValueError("AGENT_EXECUTION_MODE must be live or stub")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("AGENT_REQUEST_TIMEOUT_SECONDS must be finite and positive")
        if not math.isfinite(self.mail_send_timeout_seconds) or not 0 < self.mail_send_timeout_seconds < 35:
            raise ValueError("MAIL_SEND_TIMEOUT_SECONDS must be positive and below 35")

    @classmethod
    def from_env(cls) -> "ApiSettings":
        try:
            timeout = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "30"))
        except ValueError:
            raise ValueError("AGENT_REQUEST_TIMEOUT_SECONDS must be a number") from None
        try:
            mail_timeout = float(os.getenv("MAIL_SEND_TIMEOUT_SECONDS", "30"))
        except ValueError:
            raise ValueError("MAIL_SEND_TIMEOUT_SECONDS must be a number") from None
        return cls(
            mode=os.getenv("AGENT_EXECUTION_MODE", "live"),
            internal_api_key=os.getenv("AGENT_INTERNAL_API_KEY", ""),
            request_timeout_seconds=timeout,
            mail_send_timeout_seconds=mail_timeout,
        )
