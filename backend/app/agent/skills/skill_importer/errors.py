from __future__ import annotations

ERROR_SOURCE_INVALID = "SOURCE_INVALID"
ERROR_HTTP_ERROR = "HTTP_ERROR"
ERROR_CONNECT_FAILED = "CONNECT_FAILED"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_TOO_LARGE = "TOO_LARGE"
ERROR_PACKAGE_INVALID = "PACKAGE_INVALID"
ERROR_SKILL_MD_MISSING = "SKILL_MD_MISSING"
ERROR_HTML_NOT_SKILL = "HTML_NOT_SKILL"
ERROR_REDIRECT_LOOP = "REDIRECT_LOOP"
ERROR_GITHUB_API_ERROR = "GITHUB_API_ERROR"


class SkillImporterError(Exception):
    """Unified protocol-layer error.

    ``code`` is a machine-readable constant (ERROR_* above); ``cause`` carries
    the underlying exception for logging; ``message`` is human-readable.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause
        self.message = message
