"""Stateless parsers for processing SmartHub API payloads into Domain Models."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .models import Account, FlowDirection, MeterType, Reading, UnitOfMeasure

_LOGGER = logging.getLogger(__name__)


def parse_accounts(enhanced_accounts: list[dict[str, Any]], account_map_data: list[dict[str, Any]]) -> list[Account]:
    """Parse raw account payloads and apply meter type and service mappings.

    Args:
        enhanced_accounts: Raw accounts payload from the overview endpoint.
        account_map_data: Raw service locations and account mapping entries.

    Returns:
        A list of parsed Account domain model instances.

    """
    accounts = []
    if not isinstance(enhanced_accounts, list):
        _LOGGER.error("Expected list of accounts from overview, got %s", type(enhanced_accounts))
        return []
    for account_dict in enhanced_accounts:
        if not isinstance(account_dict, dict):
            continue
        account = Account.from_dict(account_dict)
        apply_account_map(account, account_map_data)
        accounts.append(account)
    return accounts


def apply_account_map(account: Account, account_maps: list[dict[str, Any]]) -> None:
    """Apply service location and meter type mapping onto an Account object.

    Args:
        account: The Account instance to update in-place.
        account_maps: Raw list of service location and account mapping entries.

    """
    if not isinstance(account_maps, list):
        _LOGGER.warning("Account maps is not a list: %s", type(account_maps))
        return
    for map_dict in account_maps:
        if not isinstance(map_dict, dict):
            continue
        account_number = str(map_dict.get("account") or "")

        # Primary match
        if account_number == account.primary_account_number:
            _update_account_from_map(account, map_dict)
            break

        # Secondary match for active locations
        if account_number in account.account_numbers and not map_dict.get("inactive", True):
            account.primary_account_number = account_number
            account.utility_account_id = account_number
            account.id = f"{account.customer_id}_{account_number}" if account.customer_id else account_number
            _update_account_from_map(account, map_dict)
            break


def _update_account_from_map(account: Account, map_dict: dict[str, Any]) -> None:
    """Update Account attributes using data from an account mapping entry.

    Args:
        account: The Account instance to update in-place.
        map_dict: A dictionary containing mapping data for the account.

    """
    service_location = map_dict.get("primaryServiceLocationId")
    service_locs = map_dict.get("serviceLocations")
    if not service_location and isinstance(service_locs, list) and service_locs:
        service_location = service_locs[0]

    account.service_location_number = str(service_location) if service_location else ""

    services = map_dict.get("services") or []
    if not services:
        # Fallback 1: check serviceLocationToIndustries
        industries = map_dict.get("serviceLocationToIndustries") or {}
        if isinstance(industries, dict):
            for ind_list in industries.values():
                if isinstance(ind_list, list):
                    services.extend(ind_list)
    if not services:
        # Fallback 2: check serviceToProviders keys
        service_providers = map_dict.get("serviceToProviders") or {}
        if isinstance(service_providers, dict):
            services.extend(service_providers.keys())

    if "ELECTRIC" in services or "ELEC" in services:
        account.meter_type = MeterType.ELEC
    else:
        # TODO: Add support for GAS and WATER utility types once their payload structures
        # and response values are defined/supported. Currently we default to ELECTRIC.
        _LOGGER.warning(
            "Could not determine meter type from services %s for account %s. Defaulting to ELEC.",
            services,
            account.primary_account_number,
        )
        account.meter_type = MeterType.ELEC


def parse_readings(
    data: dict[str, Any],
    account: Account,
    unit_of_measure: UnitOfMeasure,
) -> list[Reading]:
    """Parse raw utility usage JSON data into Reading objects.

    Args:
        data: Raw JSON payload returned from the utility usage polling.
        account: The Account associated with the readings.
        unit_of_measure: The unit of measure to assign to the readings.

    Returns:
        A list of parsed Reading domain model instances.

    """
    industry_key = account.meter_type.value
    industry_data = data.get(industry_key, [])

    usage_obj = next((obj for obj in industry_data if obj.get("type") == "USAGE"), None)
    cost_obj = next((obj for obj in industry_data if obj.get("type") == "COST"), None)

    if not usage_obj:
        return []

    meter_flow_map = {m.get("meterNumber"): m.get("flowDirection", "FORWARD") for m in usage_obj.get("meters", [])}

    usage_series_list = usage_obj.get("series", [])
    cost_series_list = cost_obj.get("series", []) if cost_obj else []

    # Build cost points lookup map
    cost_points_map = {}
    for cost_series in cost_series_list:
        cost_meter_id = cost_series.get("meterNumber") or cost_series.get("name")
        if not cost_meter_id:
            continue
        cost_points = cost_series.get("data", [])
        meter_costs = {}
        for cost_point in cost_points:
            point_x = cost_point.get("x")
            cost_value = cost_point.get("y")
            if point_x is not None:
                meter_costs[str(point_x)] = float(cost_value) if cost_value is not None else 0.0
        cost_points_map[cost_meter_id] = meter_costs

    readings = []
    for usage_series in usage_series_list:
        meter_id = usage_series.get("meterNumber") or usage_series.get("name")
        if not meter_id:
            continue

        flow_direction_raw = meter_flow_map.get(meter_id, "FORWARD")
        try:
            flow_direction = FlowDirection(flow_direction_raw)
        except ValueError:
            flow_direction = FlowDirection.FORWARD

        meter_costs = cost_points_map.get(meter_id, {})

        points = usage_series.get("data", [])
        intervals = usage_obj.get("xToOrderedInterval", {})

        for point in points:
            x = str(point.get("x"))
            y_val = point.get("y")
            y = float(y_val) if y_val is not None else 0.0

            interval_entry = intervals.get(x)
            if not interval_entry:
                continue
            interval_info = interval_entry.get("interval")
            if not interval_info:
                continue
            start_ms_val = interval_info.get("start")
            end_ms_val = interval_info.get("end")
            if start_ms_val is None or end_ms_val is None:
                continue

            readings.append(
                Reading(
                    start_time=datetime.fromtimestamp(start_ms_val / 1000, tz=UTC),
                    end_time=datetime.fromtimestamp(end_ms_val / 1000, tz=UTC),
                    usage=y,
                    cost=meter_costs.get(x, 0.0),
                    meter_id=meter_id,
                    meter_type=account.meter_type,
                    flow_direction=flow_direction,
                    unit_of_measure=unit_of_measure,
                )
            )
    return readings
