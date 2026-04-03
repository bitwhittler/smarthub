"""Define authentication and token management protocols."""

from __future__ import annotations

from typing import Protocol


class TokenProvider(Protocol):
    """Protocol defining the interface needed for auth token retrieval and rotation."""

    @property
    def token(self) -> str | None:
        """Return the current authorization token."""
        ...

    async def async_login(self, token_to_refresh: str | None = None) -> None:
        """Retrieve a new authorization token, optionally refreshing a stale one.

        Args:
            token_to_refresh: The stale token that triggered the refresh, if any.

        """
        ...

    def is_auth_route(self, path: str) -> bool:
        """Return True if the specified path/route is an authentication endpoint.

        Args:
            path: The URL path/route to check.

        """
        ...
