"""Core Electric Cooperative utility implementation."""

from .base import UtilityBase


class Core(UtilityBase):
    """CORE Electric Cooperative utility provider."""

    @staticmethod
    def provider_id() -> str:
        """Return the unique provider ID for CORE."""
        return "core"

    @staticmethod
    def timezone() -> str:
        """Return the literal timezone string for CORE."""
        return "America/Denver"
