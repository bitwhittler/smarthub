"""Tests for the SmartHub API client."""

import asyncio
import re
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses

from smarthub.exceptions import (
    SmartHubApiError,
    SmartHubAuthError,
    SmartHubMfaChallenge,
    SmartHubTimeoutError,
)
from smarthub.models import (
    Account,
    FlowDirection,
    MeterType,
    ReadResolution,
    UnitOfMeasure,
)
from smarthub.smarthub import SmartHub
from smarthub.utilities import UtilityBase
from smarthub.utils import align_datetime
from tests.mock_data import (
    MOCK_ACCTS,
    MOCK_LOGIN_FAIL,
    MOCK_LOGIN_SUCCESS,
    MOCK_MAP_ELECTRIC,
    MOCK_MAP_GAS,
    MOCK_USAGE_COMPLETE,
    MOCK_USAGE_PENDING,
)


@pytest.fixture
def mock_aioresponse() -> Generator[aioresponses, None, None]:
    """Fixture to provide aioresponses mock."""
    with aioresponses() as m:
        yield m


@pytest.mark.asyncio
async def test_async_login_success(mock_aioresponse: aioresponses) -> None:
    """Verify that SmartHub stores tokens internally upon successful authentication."""
    # Mock standard non-MFA flow
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()
        assert client._token == "mock-jwt-token"


@pytest.mark.asyncio
async def test_async_login_failure(mock_aioresponse: aioresponses) -> None:
    """Verify that invalid authentication tokens trigger the appropriate exceptions."""
    # Mock standard non-MFA flow
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_FAIL,
    )

    async with SmartHub("core", "user", "pass") as client:
        with pytest.raises(SmartHubAuthError):
            await client.async_login()


@pytest.mark.asyncio
async def test_accounts_merging(mock_aioresponse: aioresponses) -> None:
    """Verify that async_fetch_accounts accurately merges data from multiple endpoints."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview\?email=user"),
        status=200,
        payload=MOCK_ACCTS,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data\?userId=user"),
        status=200,
        payload=MOCK_MAP_ELECTRIC,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()
        accounts = await client.async_fetch_accounts()

        assert len(accounts) == 1
        assert accounts[0].meter_type == MeterType.ELEC


@pytest.mark.asyncio
async def test_transparent_reauth(mock_aioresponse: aioresponses) -> None:
    """Verify that 401 Unauthorized responses trigger transparent re-authentication."""
    # First request returns 401
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview\?email=user"),
        status=401,
    )

    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )

    # The interceptor calls login
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    # The interceptor instantly retries
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview\?email=user"),
        status=200,
        payload=MOCK_ACCTS,
    )

    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data\?userId=user"),
        status=200,
        payload=MOCK_MAP_ELECTRIC,
    )

    async with SmartHub("core", "user", "pass") as client:
        # Pre-seed a dummy token so the client thinks it's valid
        client._token = "stale-token"

        accounts = await client.async_fetch_accounts()
        assert len(accounts) == 1
        assert client._token == "mock-jwt-token"


@pytest.mark.asyncio
async def test_polling_intervals(mock_aioresponse: aioresponses) -> None:
    """Verify that the polling logic correctly handles PENDING statuses and retries."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )

    # We need USAGE and COST mapping trapped.
    # Returns PENDING on loop 1
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/utility-usage/poll"),
        status=200,
        payload=MOCK_USAGE_PENDING,
    )
    # Returns COMPLETE on loop 2
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/utility-usage/poll"),
        status=200,
        payload=MOCK_USAGE_COMPLETE,
    )

    # For the subsequent COST hit:
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/utility-usage/poll"),
        status=200,
        payload=MOCK_USAGE_COMPLETE,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()

        # Pull account baseline natively
        acct = Account.from_dict(
            {
                "accountNumbers": ["111222333"],
                "primaryServiceLocationId": "555666777",
                "customerId": "999000",
            }
        )
        acct.meter_type = MeterType.ELEC

        d_start = datetime(2026, 3, 26, tzinfo=UTC)
        d_end = datetime(2026, 4, 28, tzinfo=UTC)

        # Patch asyncio.sleep so the test suite doesn't physically freeze waiting for the internal retry tick.
        with patch("asyncio.sleep", new_callable=AsyncMock):
            reads = await client.async_fetch_meter_data(acct, ReadResolution.HOURLY, d_start, d_end)
            assert reads[0].meter_id == "METER1"
            assert reads[0].flow_direction == FlowDirection.FORWARD
            assert reads[0].usage == 1.5
            assert reads[0].cost == 0.25
            assert acct.has_daily is True
            assert acct.has_hourly is True
            assert acct.connect_date is not None
            assert acct.connect_date.year == 2020
            assert acct.connect_date.month == 3
            assert reads[0].usage == 1.5
            assert reads[0].unit_of_measure == UnitOfMeasure.KWH

    # Test Alignment
    unaligned_start = datetime(2026, 3, 26, 14, 7, 22, tzinfo=UTC)
    assert align_datetime(unaligned_start, ReadResolution.HOURLY, snap_up=False) == datetime(2026, 3, 26, 14, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_get_latest_meter_data(mock_aioresponse: aioresponses) -> None:
    """Verify the convenience method for fetching the latest meter data."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/utility-usage/poll"),
        status=200,
        payload=MOCK_USAGE_COMPLETE,
        repeat=True,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()
        acct = Account(
            customer_id="C123",
            customer_name="John Doe",
            account_numbers=["111222333"],
            primary_account_number="111222333",
            service_location_number="555666777",
        )

        with patch("asyncio.sleep", return_value=None):
            reads = await client.async_fetch_latest_meter_data(acct, ReadResolution.HOURLY, lookback_hours=6)
            assert len(reads) > 0
            assert reads[0].meter_id == "METER1"


@pytest.mark.asyncio
async def test_polling_timeout_handled(mock_aioresponse: aioresponses) -> None:
    """Verify runaway pending limits exit gracefully."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/utility-usage/poll"),
        status=200,
        payload=MOCK_USAGE_PENDING,
        repeat=True,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()

        acct = Account.from_dict({"accountNumbers": ["111222333"]})
        with patch("asyncio.sleep", return_value=None), pytest.raises(SmartHubTimeoutError):
            await client.async_fetch_meter_data(
                acct,
                ReadResolution.HOURLY,
                datetime.now(UTC),
                datetime.now(UTC),
            )


@pytest.mark.asyncio
async def test_500_api_error_raised(mock_aioresponse: aioresponses) -> None:
    """Verify HTTP failures throw ApiError directly."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview\?email=user"),
        status=500,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()
        with pytest.raises(SmartHubApiError):
            await client.async_fetch_accounts()


@pytest.mark.asyncio
async def test_accounts_map_failure_cascade(mock_aioresponse: aioresponses) -> None:
    """Verify the mapping endpoint gracefully falls back natively."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview\?email=user"),
        status=200,
        payload=MOCK_ACCTS,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data\?userId=user"),
        status=500,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()
        accounts = await client.async_fetch_accounts()
        # Even though mapping array failed, the account array still parsed the base!
        assert len(accounts) == 1
        assert accounts[0].service_location_number == "555666777"


@pytest.mark.asyncio
async def test_mfa_challenge_raised(mock_aioresponse: aioresponses) -> None:
    """Verify SmartHub blocks auth natively if TOTP is required but absent."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="TOTP",
    )

    async with SmartHub("core", "user", "pass") as client:
        with pytest.raises(SmartHubMfaChallenge):
            await client.async_login()


@pytest.mark.asyncio
async def test_mfa_success_injection(mock_aioresponse: aioresponses) -> None:
    """Verify SmartHub correctly hooks pyotp into the standard pipeline."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="TOTP",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )

    # Standard dummy MFA base32 secret
    dummy_secret = "JBSWY3DPEHPK3PXP"
    async with SmartHub("core", "user", "pass", dummy_secret) as client:
        await client.async_login()
        assert client._token == "mock-jwt-token"


@pytest.mark.asyncio
@pytest.mark.skip(reason="GAS and WATER support are currently disabled, only ELECTRIC is supported.")
async def test_non_electric_returned(mock_aioresponse: aioresponses) -> None:
    """Verify other meter types (GAS, WATER) are successfully parsed and returned."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview\?email=user"),
        status=200,
        payload=MOCK_ACCTS,
    )

    # Mock data for a gas account
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data\?userId=user"),
        status=200,
        payload=MOCK_MAP_GAS,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()
        accounts = await client.async_fetch_accounts()

        assert len(accounts) == 1
        assert accounts[0].meter_type == MeterType.GAS


@pytest.mark.asyncio
async def test_login_network_error_wrapped(mock_aioresponse: aioresponses) -> None:
    """Verify network exceptions during login are wrapped in SmartHubAuthError."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method"),
        exception=aiohttp.ClientConnectionError("Connection refused"),
        repeat=True,
    )

    async with SmartHub("core", "user", "pass") as client:
        with patch("asyncio.sleep", return_value=None):
            with pytest.raises(SmartHubAuthError) as exc_info:
                await client.async_login()
            assert "Failed to check TOTP method" in str(exc_info.value)


@pytest.mark.asyncio
async def test_login_invalid_json_wrapped(mock_aioresponse: aioresponses) -> None:
    """Verify invalid JSON responses during login are wrapped in SmartHubAuthError."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        body="invalid json data",
    )

    async with SmartHub("core", "user", "pass") as client:
        with pytest.raises(SmartHubAuthError) as exc_info:
            await client.async_login()
        assert "Failed to parse login response" in str(exc_info.value)


@pytest.mark.asyncio
async def test_request_network_error_wrapped(mock_aioresponse: aioresponses) -> None:
    """Verify request network exceptions are wrapped in SmartHubApiError."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview"),
        exception=aiohttp.ClientConnectionError("Connection refused"),
        repeat=True,
    )

    async with SmartHub("core", "user", "pass") as client:
        client._token = "valid-token"
        with patch("asyncio.sleep", return_value=None):
            with pytest.raises(SmartHubApiError) as exc_info:
                await client.async_fetch_accounts()
            assert "Request failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_request_invalid_json_wrapped(mock_aioresponse: aioresponses) -> None:
    """Verify request invalid JSON is wrapped in SmartHubApiError."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview"),
        status=200,
        body="not-a-json",
    )

    async with SmartHub("core", "user", "pass") as client:
        client._token = "valid-token"
        with pytest.raises(SmartHubApiError) as exc_info:
            await client.async_fetch_accounts()
        assert "Failed to parse API response as JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_meter_data_with_null_values(mock_aioresponse: aioresponses) -> None:
    """Verify fetch_meter_data gracefully handles null/None values for usage/cost."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
    )

    mock_usage_none_values = {
        "status": "COMPLETE",
        "data": {
            "ELECTRIC": [
                {
                    "type": "USAGE",
                    "connectDate": "March 10, 2020",
                    "hasDaily": True,
                    "hasHourly": True,
                    "meters": [{"meterNumber": "METER1", "flowDirection": "FORWARD"}],
                    "xToOrderedInterval": {"2026-03-26 14:00": {"interval": {"start": 1774533600000, "end": 1774537200000}}},
                    "series": [{"meterNumber": "METER1", "data": [{"x": "2026-03-26 14:00", "y": None}]}],
                },
                {
                    "type": "COST",
                    "xToOrderedInterval": {"2026-03-26 14:00": {"interval": {"start": 1774533600000, "end": 1774537200000}}},
                    "series": [{"meterNumber": "METER1", "data": [{"x": "2026-03-26 14:00", "y": None}]}],
                },
            ]
        },
    }

    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/utility-usage/poll"),
        status=200,
        payload=mock_usage_none_values,
    )

    async with SmartHub("core", "user", "pass") as client:
        await client.async_login()

        acct = Account.from_dict(
            {
                "accountNumbers": ["111222333"],
                "primaryServiceLocationId": "555666777",
                "customerId": "999000",
            }
        )
        acct.meter_type = MeterType.ELEC

        d_start = datetime(2026, 3, 26, tzinfo=UTC)
        d_end = datetime(2026, 3, 27, tzinfo=UTC)

        with patch("asyncio.sleep", return_value=None):
            reads = await client.async_fetch_meter_data(acct, ReadResolution.HOURLY, d_start, d_end)
            assert len(reads) == 1
            assert reads[0].usage == 0.0
            assert reads[0].cost == 0.0


@pytest.mark.asyncio
async def test_totp_secret_normalization(mock_aioresponse: aioresponses) -> None:
    """Verify spaces are stripped and secret is uppercased during initialization."""
    async with SmartHub("core", "user", "pass", totp_secret="j bswy 3dpe hpk3 pxp") as client:
        assert client._totp_secret == "JBSWY3DPEHPK3PXP"


@pytest.mark.asyncio
async def test_totp_secret_invalid_base32() -> None:
    """Verify that an invalid Base32 string raises ValueError."""
    with pytest.raises(ValueError, match="Invalid TOTP secret"):
        SmartHub("core", "user", "pass", totp_secret="invalid-secret-not-base32-!!!")


@pytest.mark.asyncio
async def test_timezone_conversion() -> None:
    """Verify that timezone-aware datetime inputs are correctly converted to UTC."""
    # Datetime with Eastern Time Zone (EDT is UTC-4 in March)
    eastern_dt = datetime(2026, 3, 26, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    # In UTC, this is 2026-03-26 16:00:00
    aligned = align_datetime(eastern_dt, ReadResolution.HOURLY)
    assert aligned.tzinfo == UTC
    assert aligned.hour == 16
    assert aligned.minute == 0


@pytest.mark.asyncio
async def test_concurrent_login_lock(mock_aioresponse: aioresponses) -> None:
    """Verify that concurrent async_login calls are serialized and avoid login race conditions."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method\?userId=user"),
        status=200,
        payload="NONE",
        repeat=True,
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload=MOCK_LOGIN_SUCCESS,
        repeat=True,
    )

    async with SmartHub("core", "user", "pass") as client:
        # Launch two parallel login tasks
        await asyncio.gather(
            client.async_login(),
            client.async_login(),
        )
        # Should succeed and token set
        assert client._token == "mock-jwt-token"


@pytest.mark.asyncio
async def test_totp_secret_unpadded_validation() -> None:
    """Verify unpadded Base32 secrets (length not multiple of 8) are successfully validated."""
    # "JBSWY3DPEHPK3PX" has length 15 (missing the final padding character)
    async with SmartHub("core", "user", "pass", totp_secret="JBSWY3DPEHPK3PX") as client:
        assert client._totp_secret == "JBSWY3DPEHPK3PX="


@pytest.mark.asyncio
async def test_concurrent_transparent_reauth(mock_aioresponse: aioresponses) -> None:
    """Verify concurrent 401s only trigger a single login request."""
    async with SmartHub("core", "user", "pass") as client:
        client._token = "stale-token"

        def overview_callback(url: Any, **kwargs: Any) -> CallbackResult:
            headers = kwargs.get("headers") or {}
            auth = headers.get("Authorization") or ""
            if "stale-token" in auth:
                return CallbackResult(status=401)
            return CallbackResult(status=200, payload=MOCK_ACCTS)  # type: ignore[arg-type]

        mock_aioresponse.get(
            re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview"),
            callback=overview_callback,
            repeat=True,
        )
        mock_aioresponse.get(
            re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method"),
            status=200,
            payload="NONE",
        )
        # Mock OAuth login endpoint only ONCE (no repeat=True). If a duplicate post occurs, it raises a mock error.
        mock_aioresponse.post(
            re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
            status=200,
            payload=MOCK_LOGIN_SUCCESS,
        )
        mock_aioresponse.get(
            re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data"),
            status=200,
            payload=MOCK_MAP_ELECTRIC,
            repeat=True,
        )

        await asyncio.gather(
            client.async_fetch_accounts(),
            client.async_fetch_accounts(),
        )
        assert client._token == "mock-jwt-token"


class CustomTestUtility(UtilityBase):
    """Test custom utility provider implementation."""

    @staticmethod
    def provider_id() -> str:
        """Return provider ID."""
        return "custom-test-provider"

    def base_url(self) -> str:
        """Return base URL."""
        return "https://custom.smarthub.coop"

    @staticmethod
    def timezone() -> str:
        """Return timezone."""
        return "America/New_York"


@pytest.mark.asyncio
async def test_constructor_injection_dip() -> None:
    """Verify constructor injection accepts a UtilityBase subclass instance (DIP)."""
    custom_util = CustomTestUtility()
    async with SmartHub(custom_util, "user", "pass") as client:
        assert client.utility is custom_util
        assert client.provider_id == "custom-test-provider"
        assert client._base_url == "https://custom.smarthub.coop"


@pytest.mark.asyncio
async def test_request_timeout_retry_wrapped(mock_aioresponse: aioresponses) -> None:
    """Verify that request timeouts trigger retries and wrap into SmartHubApiError."""
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview"),
        exception=TimeoutError("Request timed out"),
        repeat=True,
    )
    async with SmartHub("core", "user", "pass") as client:
        client._token = "valid-token"
        with patch("asyncio.sleep", return_value=None):
            with pytest.raises(SmartHubApiError) as exc_info:
                await client.async_fetch_accounts()
            assert "Request failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_stored_token_reuse(mock_aioresponse: aioresponses) -> None:
    """Verify that initializing with a token uses it directly without logging in."""
    called = False

    def overview_callback(url: Any, **kwargs: Any) -> CallbackResult:
        nonlocal called
        called = True
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer preset-token"
        return CallbackResult(status=200, payload=MOCK_ACCTS)  # type: ignore[arg-type]

    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview"),
        callback=overview_callback,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data"),
        status=200,
        payload=MOCK_MAP_ELECTRIC,
    )

    async with SmartHub("core", "user", "pass", token="preset-token") as client:
        assert client._token == "preset-token"
        accounts = await client.async_fetch_accounts()
        assert len(accounts) == 1
        assert called is True


@pytest.mark.asyncio
async def test_token_expiry_and_callback(mock_aioresponse: aioresponses) -> None:
    """Verify that expired tokens trigger transparent re-auth and invoke callback."""
    updated_tokens = []

    def save_token(token: str) -> None:
        updated_tokens.append(token)

    # Initial GET fails with 401, retry succeeds
    def overview_callback(url: Any, **kwargs: Any) -> CallbackResult:
        headers = kwargs.get("headers") or {}
        auth = headers.get("Authorization") or ""
        if "expired-token" in auth:
            return CallbackResult(status=401)
        return CallbackResult(status=200, payload=MOCK_ACCTS)  # type: ignore[arg-type]

    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/exposed/customer-overview"),
        callback=overview_callback,
        repeat=True,
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method"),
        status=200,
        payload="NONE",
        repeat=True,
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload={"status": "SUCCESS", "authorizationToken": "newly-acquired-token"},
    )
    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/secured/user-data"),
        status=200,
        payload=MOCK_MAP_ELECTRIC,
        repeat=True,
    )

    async with SmartHub("core", "user", "pass", token="expired-token", on_token_updated=save_token) as client:
        accounts = await client.async_fetch_accounts()
        assert len(accounts) == 1
        assert client._token == "newly-acquired-token"
        assert updated_tokens == ["newly-acquired-token"]


@pytest.mark.asyncio
async def test_token_callback_async_and_error_handling(mock_aioresponse: aioresponses) -> None:
    """Verify callback supports async functions and handles errors gracefully."""
    called_with = None

    async def async_callback(token: str) -> None:
        nonlocal called_with
        called_with = token
        raise ValueError("Callback simulation failure")

    mock_aioresponse.get(
        re.compile(r"^https://.*\.smarthub\.coop/services/two-factor/method"),
        status=200,
        payload="NONE",
    )
    mock_aioresponse.post(
        re.compile(r"^https://.*\.smarthub\.coop/services/oauth/auth/v2"),
        status=200,
        payload={"status": "SUCCESS", "authorizationToken": "async-token"},
    )

    async with SmartHub("core", "user", "pass", on_token_updated=async_callback) as client:
        # Trigger login to trigger callback
        await client.async_login()
        assert client._token == "async-token"
        assert called_with == "async-token"
