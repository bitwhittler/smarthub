"""Dynamic utility discovery and selection."""

from .base import UtilityBase as UtilityBase
from .core import Core as Core

__all__ = ["UtilityBase", "get_all_utilities", "select_utility"]


def get_all_utilities() -> list[type[UtilityBase]]:
    """Return all utility subclasses found in this package."""
    return UtilityBase.subclasses


def select_utility(name: str) -> type[UtilityBase]:
    """Select the utility class matching the provided name or provider ID."""
    for utility in get_all_utilities():
        if name.lower() in [utility.provider_id().lower(), utility.__name__.lower()]:
            return utility
    raise ValueError(f"Utility provider '{name}' not found. Please implement it under utilities/{name}.py")
