"""Exception types for the webchat wrapper."""


class WebchatError(RuntimeError):
    """Base class for all webchat errors."""


class ConfigError(WebchatError):
    """Missing or invalid configuration (token / session not found)."""


class AuthError(WebchatError):
    """Authentication failed or session expired."""


class RateLimitError(WebchatError):
    """The upstream service rate-limited or risk-controlled the request.

    Attributes:
        code: upstream numeric error code (e.g. Doubao 710022002).
    """

    def __init__(self, message: str, code: int = 0, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


class CaptchaError(RateLimitError):
    """Upstream requires a manual captcha / verification before continuing."""


class PoWError(WebchatError):
    """DeepSeek proof-of-work challenge could not be solved."""


class ProviderError(WebchatError):
    """Unknown provider or provider-specific setup problem."""


class UploadError(WebchatError):
    """File / image upload failed."""
