"""Error type shared by the release domain and its HTTP adapters."""

from __future__ import annotations


class ReleaseError(Exception):
    """Expected, safe-to-return API error."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
