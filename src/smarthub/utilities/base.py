"""Base class for SmartHub utility provider implementations."""

import abc
from typing import Any, ClassVar


class UtilityBase(abc.ABC):
    """Abstract base class for all utility providers."""

    subclasses: ClassVar[list[type["UtilityBase"]]] = []

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register subclass implementations dynamically."""
        super().__init_subclass__(**kwargs)
        if cls not in cls.subclasses:
            cls.subclasses.append(cls)

    @staticmethod
    @abc.abstractmethod
    def provider_id() -> str:
        """Return the unique provider ID for the utility (e.g., 'core')."""
        raise NotImplementedError

    def base_url(self) -> str:
        """Return the base URL for the SmartHub API endpoint."""
        return f"https://{self.provider_id()}.smarthub.coop"

    @staticmethod
    def timezone() -> str:
        """Return the literal timezone string. SmartHub mostly binds to UTC timestamps natively, but allows overrides."""
        return "UTC"
