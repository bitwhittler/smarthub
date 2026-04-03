"""Tests for smarthub models."""

from datetime import UTC, datetime

from smarthub.models import Account, MeterType, Reading, ReadResolution, UnitOfMeasure
from tests.mock_data import MOCK_ACCOUNT


def test_account_mapping_valid() -> None:
    """Verify Account object instantiates correctly."""
    acct = Account.from_dict(MOCK_ACCOUNT)

    assert acct.customer_id == "999000"
    assert acct.customer_name == "JOHN DOE"
    assert acct.primary_account_number == "111222333"
    assert acct.service_location_number == "555666777"
    assert acct.id == "999000_111222333"
    assert acct.utility_account_id == "111222333"


def test_account_mapping_missing_fields() -> None:
    """Verify empty datasets gracefully map Nones instead of dropping KeyErrors."""
    partial_data = {"customerId": "100", "accountNumbers": ["12"]}

    acct = Account.from_dict(partial_data)

    assert acct.customer_id == "100"
    assert acct.customer_name == ""
    assert acct.primary_account_number == "12"
    assert acct.id == "100_12"
    assert acct.service_location_number == ""


def test_meter_initialization() -> None:
    """Verify class objects instantiate cleanly natively."""
    acct = Account(customer_id="1", customer_name="Test", account_numbers=["1"], meter_type=MeterType.ELEC)

    assert acct.meter_type == MeterType.ELEC
    assert acct.meter_type.value == "ELECTRIC"


def test_reading_model() -> None:
    """Verify Reading object instantiates correctly."""
    now = datetime.now(UTC)
    reading = Reading(start_time=now, end_time=now, usage=10.5, cost=1.25, unit_of_measure=UnitOfMeasure.KWH)
    assert reading.usage == 10.5
    assert reading.cost == 1.25
    assert reading.unit_of_measure == UnitOfMeasure.KWH


def test_enums_string_formatting() -> None:
    """Verify Enums correctly drop strings instead of python objects natively."""
    assert str(MeterType.ELEC) == "ELECTRIC"
    assert str(ReadResolution.HOURLY) == "HOURLY"
    assert str(UnitOfMeasure.KWH) == "KWH"


def test_apply_account_map_fallbacks() -> None:
    """Verify that apply_account_map resolves services through fallbacks when services list is empty."""
    from smarthub.parsers import apply_account_map

    # Test case 1: services list is populated
    acct1 = Account(customer_id="1", customer_name="Test", account_numbers=["1"], primary_account_number="1")
    apply_account_map(acct1, [{"account": "1", "services": ["ELEC"]}])
    assert acct1.meter_type == MeterType.ELEC

    # Test case 2: services list is empty, fallback to serviceLocationToIndustries
    acct2 = Account(customer_id="2", customer_name="Test", account_numbers=["2"], primary_account_number="2")
    apply_account_map(acct2, [{"account": "2", "services": [], "serviceLocationToIndustries": {"loc1": ["ELECTRIC"]}}])
    assert acct2.meter_type == MeterType.ELEC

    # Test case 3: services list is empty, fallback to serviceToProviders
    acct3 = Account(customer_id="3", customer_name="Test", account_numbers=["3"], primary_account_number="3")
    apply_account_map(acct3, [{"account": "3", "services": [], "serviceToProviders": {"ELEC": ["CORE"]}}])
    assert acct3.meter_type == MeterType.ELEC

    # Test case 4: services list is empty, serviceLocationToIndustries is invalid type (string)
    acct4 = Account(customer_id="4", customer_name="Test", account_numbers=["4"], primary_account_number="4")
    apply_account_map(acct4, [{"account": "4", "services": [], "serviceLocationToIndustries": "invalid_type"}])
    assert acct4.meter_type == MeterType.ELEC

    # Test case 5: services list is empty, serviceToProviders is invalid type (string)
    acct5 = Account(customer_id="5", customer_name="Test", account_numbers=["5"], primary_account_number="5")
    apply_account_map(acct5, [{"account": "5", "services": [], "serviceToProviders": "invalid_type"}])
    assert acct5.meter_type == MeterType.ELEC
