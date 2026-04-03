"""Models for the SmartHub API."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ReadResolution(Enum):
    """The time resolution of the reading.

    Note: SmartHub's API reports ``HOURLY`` as 15-minute interval data,
    not true 60-minute buckets.  The ``align_datetime`` helper snaps to
    quarter-hour boundaries when this resolution is selected.
    """

    MONTHLY = "MONTHLY"
    DAILY = "DAILY"
    HOURLY = "HOURLY"

    def __str__(self) -> str:
        """Return the string representation of the resolution."""
        return self.value


class MeterType(Enum):
    """The type of meter."""

    ELEC = "ELECTRIC"
    GAS = "GAS"
    WATER = "WATER"

    def __str__(self) -> str:
        """Return the string representation of the meter type."""
        return self.value


class UnitOfMeasure(Enum):
    """The unit of measure for a reading."""

    KWH = "KWH"
    # TODO: Add other units of measure (e.g., CCF, THERMS, KGAL) here in the future
    # once support for gas, water, or other utilities is added.

    def __str__(self) -> str:
        """Return the string representation of the unit of measure."""
        return self.value


class FlowDirection(Enum):
    """The direction of energy flow."""

    FORWARD = "FORWARD"
    REVERSE = "REVERSE"

    def __str__(self) -> str:
        """Return the string representation of the flow direction."""
        return self.value


@dataclass(slots=True)
class Reading:
    """A single usage reading."""

    start_time: datetime
    end_time: datetime
    usage: float
    unit_of_measure: UnitOfMeasure
    meter_id: str = ""
    meter_type: MeterType = MeterType.ELEC
    flow_direction: FlowDirection = FlowDirection.FORWARD
    cost: float = 0.0


@dataclass(slots=True)
class Account:
    """A SmartHub account."""

    customer_id: str
    customer_name: str
    account_numbers: list[str]
    type: str | None = None

    # Stored for usage polling
    primary_account_number: str = ""
    service_location_number: str = ""

    # Smarthub standard identity fields
    id: str = ""
    utility_account_id: str = ""
    meter_type: MeterType = MeterType.ELEC

    # Service Metadata from poll responses
    connect_date: datetime | None = None
    has_daily: bool = False
    has_hourly: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        """Create an Account instance from a SmartHub customer-overview dictionary."""
        account_numbers = data.get("accountNumbers") or []
        primary_account = account_numbers[0] if account_numbers else ""
        customer_id = data.get("customerId", "")

        return cls(
            customer_id=customer_id,
            customer_name=data.get("customerName", ""),
            account_numbers=account_numbers,
            type=data.get("type"),
            primary_account_number=primary_account,
            service_location_number=data.get("primaryServiceLocationId", ""),
            id=f"{customer_id}_{primary_account}" if customer_id and primary_account else primary_account,
            utility_account_id=primary_account,
        )
