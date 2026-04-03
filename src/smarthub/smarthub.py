"""SmartHub API Client."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp
import pyotp

from .exceptions import SmartHubApiError, SmartHubAuthError, SmartHubMfaChallenge, SmartHubTimeoutError
from .models import Account, MeterType, Reading, ReadResolution, UnitOfMeasure
from .parsers import parse_accounts, parse_readings
from .utilities import UtilityBase, select_utility
from .utils import align_datetime

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from .protocol import TokenProvider

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class SmartHubHttpTransport:
    """Manages low-level session requests, timeout safety, and HTTP error wrapping."""

    def __init__(self, utility: UtilityBase, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize HTTP transport helper.

        Args:
            utility: The utility provider configuration instance.
            session: Optional pre-existing aiohttp ClientSession to use.

        """
        self.utility = utility
        self._base_url = utility.base_url()
        self._session = session
        self._close_session = False
        self.auth_manager: TokenProvider | None = None

    def get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp ClientSession.

        Returns:
            The active ClientSession instance.

        """
        if not self._session:
            headers = {"User-Agent": USER_AGENT}
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
            self._close_session = True
        return self._session

    async def close(self) -> None:
        """Close the HTTP session if it was created by this transport."""
        if self._session and self._close_session:
            await self._session.close()
        self._session = None
        self._close_session = False

    async def _async_raise_for_status(self, resp: aiohttp.ClientResponse, method: str, url: str) -> None:
        """Extract error details and raise SmartHubApiError if status is not successful.

        Args:
            resp: The client response to inspect.
            method: The HTTP method used for the request.
            url: The requested URL.

        Raises:
            SmartHubApiError: If the HTTP status is not 200 or 201.

        """
        if resp.status in (200, 201):
            return

        error_detail = ""
        # Redact query parameters containing sensitive user ID/credential details from the exception message
        parsed_url = urlparse(url)
        if parsed_url.query:
            params = []
            for k, v in parse_qsl(parsed_url.query):
                if k in ("userId", "email", "password", "twoFactorCode"):
                    params.append((k, "REDACTED"))
                else:
                    params.append((k, v))
            redacted_query = urlencode(params)
            parsed_url = parsed_url._replace(query=redacted_query)
        redacted_url = urlunparse(parsed_url)

        # Do not extract response body for auth routes to prevent credential echoing in tracebacks
        is_auth_route = self.auth_manager.is_auth_route(parsed_url.path) if self.auth_manager else False

        if not is_auth_route:
            try:
                body_text = await resp.text()
                if body_text:
                    try:
                        error_json = json.loads(body_text)
                        if isinstance(error_json, dict):
                            error_detail = error_json.get("message") or error_json.get("error") or body_text[:200]
                        else:
                            error_detail = body_text[:200]
                    except Exception:
                        error_detail = body_text[:200]
            except Exception:  # noqa: S110
                pass

        msg = f"API {method} {redacted_url} failed with status {resp.status}"
        if error_detail:
            msg += f": {error_detail}"
        raise SmartHubApiError(msg)

    async def request_json(self, method: str, url_or_path: str, **kwargs: Any) -> Any:  # noqa: PLR0912, PLR0915
        """Wrap transparent authentication token rotation on 401s and execute JSON requests.

        Args:
            method: The HTTP method (e.g., "GET", "POST").
            url_or_path: The absolute URL or relative API endpoint path.
            **kwargs: Additional keyword arguments passed to the request.

        Returns:
            The parsed JSON response.

        Raises:
            SmartHubAuthError: If authentication or re-authentication fails.
            SmartHubApiError: If the request fails or returns an error status.

        """
        session = self.get_session()
        if url_or_path.startswith(("http://", "https://")):
            url = url_or_path
            path = url_or_path[len(self._base_url) :].lstrip("/")
        else:
            url = f"{self._base_url}/{url_or_path.lstrip('/')}"
            path = url_or_path

        auth_manager = self.auth_manager
        is_auth_route = auth_manager.is_auth_route(path) if auth_manager else False

        # Proactive login if auth_manager is present and has no token
        if not is_auth_route and auth_manager and not auth_manager.token:
            _LOGGER.debug("No token available. Proactively authenticating...")
            try:
                await auth_manager.async_login()
            except Exception as err:
                raise SmartHubAuthError(f"Proactive login failed: {err}") from err

        max_attempts = 2
        failed_token = None
        for attempt in range(max_attempts):
            if not is_auth_route and auth_manager and auth_manager.token:
                if "headers" not in kwargs:
                    kwargs["headers"] = {}
                else:
                    kwargs["headers"] = dict(kwargs["headers"])
                kwargs["headers"]["Authorization"] = f"Bearer {auth_manager.token}"

            try:
                async with session.request(method, url, **kwargs) as resp:
                    if resp.status == 401 and not is_auth_route and auth_manager and attempt == 0:
                        _LOGGER.info("SmartHub API returned 401 Unauthorized. Attempting transparent re-login...")
                        failed_token = auth_manager.token
                        await resp.read()  # Consume response safely before retry
                        break

                    await self._async_raise_for_status(resp, method, url)
                    try:
                        return await resp.json()
                    except (aiohttp.ContentTypeError, ValueError) as err:
                        raise SmartHubApiError(f"Failed to parse API response as JSON: {err}") from err
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt == max_attempts - 1:
                    raise SmartHubApiError(f"Request failed: {err}") from err
                _LOGGER.warning("Request failed (attempt %d/%d), retrying: %s", attempt + 1, max_attempts, err)
                delay = 1.0 * (2**attempt) + random.uniform(0.0, 0.5)  # noqa: S311
                await asyncio.sleep(delay)

        if failed_token is not None and auth_manager:
            try:
                await auth_manager.async_login(token_to_refresh=failed_token)
            except SmartHubAuthError:
                raise
            except Exception as err:
                raise SmartHubAuthError(f"Re-authentication failed: {err}") from err

            # Inject the freshly acquired token into the retry payload
            if auth_manager.token:
                if "headers" not in kwargs:
                    kwargs["headers"] = {}
                else:
                    kwargs["headers"] = dict(kwargs["headers"])
                kwargs["headers"]["Authorization"] = f"Bearer {auth_manager.token}"

            try:
                async with session.request(method, url, **kwargs) as resp:
                    await self._async_raise_for_status(resp, method, url)
                    try:
                        return await resp.json()
                    except (aiohttp.ContentTypeError, ValueError) as err:
                        raise SmartHubApiError(f"Failed to parse API response as JSON after re-auth: {err}") from err
            except (aiohttp.ClientError, TimeoutError) as err:
                raise SmartHubApiError(f"Re-auth retry failed: {err}") from err

        raise SmartHubApiError("Unexpected control flow end: request was not completed.")


class SmartHubAuthManager:
    """Manages authorization tokens, token synchronization, and MFA/TOTP computation."""

    def __init__(
        self,
        transport: SmartHubHttpTransport,
        username: str,
        password: str,
        totp_secret: str | None = None,
        token: str | None = None,
        on_token_updated: Callable[[str], Any] | None = None,
    ) -> None:
        """Initialize Authentication Manager.

        Args:
            transport: The HTTP transport layer used for requests.
            username: The SmartHub username (typically an email address).
            password: The SmartHub account password.
            totp_secret: Optional Base32 multi-factor authentication (TOTP) secret.
            token: Optional previously acquired authorization token.
            on_token_updated: Optional callback to invoke when token updates.

        """
        self.transport = transport
        self._username = username
        self._password = password
        self._totp_secret = totp_secret
        self._token = token
        self.on_token_updated = on_token_updated
        self._login_lock = asyncio.Lock()

    def __repr__(self) -> str:
        """Return safe string representation without credentials."""
        return f"<SmartHubAuthManager username={self._username!r}>"

    @property
    def token(self) -> str | None:
        """Return the current authorization token."""
        return self._token

    @token.setter
    def token(self, val: str | None) -> None:
        """Set the current authorization token."""
        self._token = val

    def is_auth_route(self, path: str) -> bool:
        """Return True if the specified path/route is an authentication endpoint.

        Args:
            path: The URL path or route to check.

        Returns:
            True if the path is an authentication endpoint, False otherwise.

        """
        clean_path = path.split("?", maxsplit=1)[0].lstrip("/")
        return clean_path in (
            "services/oauth/auth/v2",
            "services/two-factor/method",
        )

    async def _async_get_totp_code(self) -> str | None:
        """Fetch and generate a TOTP code if required.

        Returns:
            The generated 6-digit TOTP code, or None if TOTP is not required.

        Raises:
            SmartHubMfaChallenge: If MFA is required but no TOTP secret is configured.
            SmartHubAuthError: If TOTP generation fails.

        """
        try:
            method_body = await self.transport.request_json(
                "GET", "services/two-factor/method", params={"userId": self._username}
            )
            if method_body != "TOTP":
                return None

            if not self._totp_secret:
                raise SmartHubMfaChallenge("Two-Factor Authentication required but no totp_secret was provided.")
            try:
                totp = pyotp.TOTP(self._totp_secret)
                return totp.now()
            except Exception as err:
                raise SmartHubAuthError(f"Failed to generate TOTP code: {err}") from err
        except SmartHubApiError as err:
            if "Failed to parse API response as JSON" in str(err):
                raise SmartHubAuthError(f"Failed to parse TOTP method response: {err}") from err
            raise SmartHubAuthError(f"Failed to check TOTP method: {err}") from err

    async def async_login(self, token_to_refresh: str | None = None) -> None:
        """Authenticate with SmartHub using credentials and TOTP checks.

        Args:
            token_to_refresh: The token to refresh. If set, login will only proceed
                if the current token matches it, preventing redundant auth calls.

        Raises:
            SmartHubAuthError: If authentication credentials are invalid or the request fails.
            SmartHubMfaChallenge: If MFA is required but no TOTP secret is configured.

        """
        if not self._username or not self._password:
            raise SmartHubAuthError("Username and password must be provided during initialization.")

        async with self._login_lock:
            if token_to_refresh is not None and self._token != token_to_refresh and self._token is not None:
                _LOGGER.info("Token already refreshed by another concurrent task while waiting for lock.")
                return
            if token_to_refresh is None and self._token is not None:
                _LOGGER.info("Proactive login already completed by another task.")
                return

            two_factor_code = await self._async_get_totp_code()

            payload = {
                "userId": self._username,
                "password": self._password,
            }
            if two_factor_code:
                payload["twoFactorCode"] = two_factor_code

            # Perform oauth post login
            try:
                json_data = await self.transport.request_json(
                    "POST", "services/oauth/auth/v2", data=payload, headers={"User-Agent": USER_AGENT}
                )
                if not isinstance(json_data, dict):
                    raise SmartHubAuthError(f"Unexpected JSON response format from login: {type(json_data)}")

                if json_data.get("status") != "SUCCESS" or "authorizationToken" not in json_data:
                    safe_keys = {"status", "message", "errorCode", "isBusinessUser"}
                    sanitized_json = {k: v for k, v in json_data.items() if k in safe_keys}
                    _LOGGER.debug("Oauth login response payload: %s", sanitized_json)
                    msg = json_data.get("message") or "Invalid credentials or unexpected response"
                    raise SmartHubAuthError(msg)

                self._token = json_data["authorizationToken"]
                _LOGGER.debug("Successfully acquired new authorization JWT.")
                if self.on_token_updated:
                    try:
                        res = self.on_token_updated(self._token)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as err:
                        _LOGGER.warning("Token update callback failed: %s", err)
            except SmartHubApiError as err:
                if "Failed to parse API response as JSON" in str(err):
                    raise SmartHubAuthError(f"Failed to parse login response: {err}") from err
                raise SmartHubAuthError(f"Login request failed: {err}") from err


class SmartHub:
    """Facade for the SmartHub API library matching downstream integrations."""

    def __init__(
        self,
        provider: str | UtilityBase,
        username: str,
        password: str,
        totp_secret: str | None = None,
        session: aiohttp.ClientSession | None = None,
        token: str | None = None,
        on_token_updated: Callable[[str], Any] | None = None,
    ) -> None:
        """Initialize the facade and set up underlying transport and auth managers.

        Args:
            provider: Either a provider ID string or a UtilityBase configuration instance.
            username: The SmartHub username/email.
            password: The SmartHub password.
            totp_secret: Optional Base32 MFA (TOTP) secret.
            session: Optional custom aiohttp ClientSession to use.
            token: Optional previously acquired authorization token.
            on_token_updated: Optional callback to invoke when token updates.

        Raises:
            ValueError: If the TOTP secret is provided but is invalid Base32.

        """
        if isinstance(provider, UtilityBase):
            self.utility = provider
        else:
            self.utility = select_utility(provider)()

        self.provider_id = self.utility.provider_id()
        self._username = username
        self._password = password
        self._totp_secret: str | None = None
        if totp_secret:
            normalized = totp_secret.replace(" ", "").upper()
            padding_needed = len(normalized) % 8
            padded = normalized + "=" * (8 - padding_needed) if padding_needed else normalized
            try:
                base64.b32decode(padded)
            except Exception as err:
                raise ValueError("Invalid TOTP secret. Must be a valid Base32 string.") from err
            self._totp_secret = padded

        self.transport = SmartHubHttpTransport(self.utility, session)
        self.auth = SmartHubAuthManager(
            self.transport,
            self._username,
            self._password,
            self._totp_secret,
            token=token,
            on_token_updated=on_token_updated,
        )
        self.transport.auth_manager = self.auth

    def __repr__(self) -> str:
        """Return safe string representation without credentials."""
        return f"<SmartHub provider={self.provider_id!r} username={self._username!r}>"

    @property
    def _base_url(self) -> str:
        return self.transport._base_url

    @property
    def _session(self) -> aiohttp.ClientSession | None:
        return self.transport._session

    @_session.setter
    def _session(self, val: aiohttp.ClientSession | None) -> None:
        self.transport._session = val

    @property
    def _close_session(self) -> bool:
        return self.transport._close_session

    @_close_session.setter
    def _close_session(self, val: bool) -> None:
        self.transport._close_session = val

    @property
    def _token(self) -> str | None:
        return self.auth.token

    @_token.setter
    def _token(self, val: str | None) -> None:
        self.auth.token = val

    @property
    def _login_lock(self) -> asyncio.Lock:
        return self.auth._login_lock

    async def __aenter__(self) -> SmartHub:
        """Enter context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit context manager."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session."""
        await self.transport.close()

    async def async_login(self, token_to_refresh: str | None = None) -> None:
        """Log in to SmartHub.

        Args:
            token_to_refresh: The token to refresh. If set, login will only proceed
                if the current token matches it, preventing redundant auth calls.

        Raises:
            SmartHubAuthError: If authentication credentials are invalid or the request fails.
            SmartHubMfaChallenge: If MFA is required but no TOTP secret is configured.

        """
        await self.auth.async_login(token_to_refresh)

    async def _async_request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        """Expose request helper for backward compatibility.

        Args:
            method: The HTTP method.
            url: The endpoint URL.
            **kwargs: Extra arguments for the HTTP request.

        Returns:
            The parsed JSON response.

        """
        return await self.transport.request_json(method, url, **kwargs)

    async def async_fetch_accounts(self) -> list[Account]:
        """Fetch all accounts and their detailed mappings.

        Returns:
            A list of parsed Account domain model instances.

        Raises:
            SmartHubAuthError: If the client is not authenticated.
            SmartHubApiError: If the server API request fails.

        """
        if not self._token or not self._username:
            raise SmartHubAuthError("Not logged in. Call async_login first.")

        headers = {
            "X-NISC-SMARTHUB-USERNAME": self._username,
            "Accept": "application/json",
        }

        enhanced_accounts, acct_map_data = await asyncio.gather(
            self._async_fetch_accounts(headers),
            self._async_fetch_account_maps(headers),
        )

        return parse_accounts(enhanced_accounts, acct_map_data)

    async def _async_fetch_accounts(self, headers: dict[str, str]) -> list[dict[str, Any]]:
        """Fetch raw account data from the customer-overview endpoint.

        Args:
            headers: HTTP headers to include with the request.

        Returns:
            A list of raw account data dictionaries.

        """
        url_overview = f"{self._base_url}/services/secured/exposed/customer-overview"
        params = {"email": self._username}

        _LOGGER.debug("Fetching account overview from %s", url_overview)
        return cast(
            "list[dict[str, Any]]",
            await self._async_request_json("GET", url_overview, headers=headers, params=params),
        )

    async def _async_fetch_account_maps(self, headers: dict[str, str]) -> list[dict[str, Any]]:
        """Fetch internal service location and meter mapping data.

        Args:
            headers: HTTP headers to include with the request.

        Returns:
            A list of raw account mapping dictionaries.

        """
        url_acct_map = f"{self._base_url}/services/secured/user-data"
        try:
            _LOGGER.debug("Fetching user data maps from %s", url_acct_map)
            return cast(
                "list[dict[str, Any]]",
                await self._async_request_json("GET", url_acct_map, headers=headers, params={"userId": self._username}),
            )
        except SmartHubApiError as err:
            _LOGGER.warning("Map fetch failed, cascading blindly: %s", err)
            return []

    async def _async_poll_utility_usage(
        self, account: Account, read_resolution: ReadResolution, start_datetime_ms: int, end_datetime_ms: int
    ) -> dict[str, Any]:
        """Poll utility-usage endpoint until complete.

        Args:
            account: The Account instance to poll usage for.
            read_resolution: The resolution of the readings.
            start_datetime_ms: Start timestamp in milliseconds.
            end_datetime_ms: End timestamp in milliseconds.

        Returns:
            The raw dictionary containing the completed utility usage data.

        Raises:
            SmartHubApiError: If the API returns an unexpected status or error.
            SmartHubTimeoutError: If the poll exceeds the maximum number of retries.

        """
        url = f"{self._base_url}/services/secured/utility-usage/poll"
        headers = {
            "X-NISC-SMARTHUB-USERNAME": self._username,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        payload = {
            "timeFrame": str(read_resolution),
            "userId": self._username,
            "screen": "USAGE_EXPLORER",
            "includeDemand": True,
            "serviceLocationNumber": account.service_location_number,
            "accountNumber": account.primary_account_number,
            "industries": [account.meter_type.value],
            "startDateTime": start_datetime_ms,
            "endDateTime": end_datetime_ms,
        }

        industry_key = account.meter_type.value
        max_retries = 30
        for loop_idx in range(max_retries):
            _LOGGER.debug("Polling %s for %s usage (loop %d/%d)...", url, industry_key, loop_idx + 1, max_retries)
            data = await self._async_request_json("POST", url, headers=headers, json=payload)

            status = data.get("status")
            if status == "COMPLETE":
                _LOGGER.debug("Poll returned COMPLETE flag!")

                industry_data = data.get("data", {}).get(industry_key, [])
                if industry_data:
                    meta = industry_data[0]
                    account.has_daily = meta.get("hasDaily", False)
                    account.has_hourly = meta.get("hasHourly", False)

                    if connect_date_raw := meta.get("connectDate"):
                        try:
                            account.connect_date = datetime.strptime(connect_date_raw, "%B %d, %Y").replace(tzinfo=UTC)
                        except (ValueError, TypeError):
                            _LOGGER.warning("Failed to parse connectDate: %s", connect_date_raw)

                return cast("dict[str, Any]", data.get("data", {}))
            if status == "PENDING":
                await asyncio.sleep(1.0 + random.uniform(0.0, 0.5))  # noqa: S311
            else:
                raise SmartHubApiError(f"Unexpected poll status: {status}")

        raise SmartHubTimeoutError("Timed out waiting for usage data to process")

    async def _async_fetch_combined_reads(
        self,
        account: Account,
        read_resolution: ReadResolution,
        start_date: datetime,
        end_date: datetime,
        unit_of_measure: UnitOfMeasure,
    ) -> list[Reading]:
        """Fetch historical usage and cost readings concurrently in 30-day slices.

        Args:
            account: The Account instance to fetch readings for.
            read_resolution: The resolution of the readings.
            start_date: The start date and time.
            end_date: The end date and time.
            unit_of_measure: The unit of measure to assign to the readings.

        Returns:
            A sorted list of combined Reading objects.

        """
        chunks = []
        current_start = start_date

        # Dynamically size slices based on read resolution to optimize API requests
        if read_resolution == ReadResolution.HOURLY:
            slice_days = 30
        elif read_resolution == ReadResolution.DAILY:
            slice_days = 365
        else:  # MONTHLY
            slice_days = 365 * 5

        while current_start < end_date:
            current_end = current_start + timedelta(days=slice_days)
            current_end = min(current_end, end_date)
            chunks.append((current_start, current_end))
            current_start = current_end

        semaphore = asyncio.Semaphore(4)

        async def fetch_chunk(chunk_start: datetime, chunk_end: datetime) -> list[Reading]:
            async with semaphore:
                start_ms = int(chunk_start.timestamp() * 1000)
                end_ms = int(chunk_end.timestamp() * 1000)

                data = await self._async_poll_utility_usage(account, read_resolution, start_ms, end_ms)
                return parse_readings(data, account, unit_of_measure)

        tasks = [asyncio.create_task(fetch_chunk(c[0], c[1])) for c in chunks]
        try:
            results = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            raise

        all_readings = []
        for chunk_readings in results:
            all_readings.extend(chunk_readings)

        # Deduplicate readings sharing the same start_time and meter_id
        seen = set()
        deduped_readings = []
        for r in all_readings:
            key = (r.start_time, r.meter_id)
            if key not in seen:
                seen.add(key)
                deduped_readings.append(r)

        deduped_readings.sort(key=lambda reading: reading.start_time)
        return deduped_readings

    async def async_fetch_meter_data(
        self, account: Account, read_resolution: ReadResolution, start_date: datetime, end_date: datetime
    ) -> list[Reading]:
        """Fetch and combine usage and cost readings for a specific timeframe.

        Args:
            account: The Account instance to fetch readings for.
            read_resolution: The resolution of the readings.
            start_date: The start date and time.
            end_date: The end date and time.

        Returns:
            A list of combined Reading objects.

        """
        _LOGGER.debug("Targeting meter data mapping for %s", account.primary_account_number)
        uom_map = {
            MeterType.ELEC: UnitOfMeasure.KWH,
            # TODO: Add mappings for GAS and WATER here in the future when their
            # data structures and units of measure are supported.
        }
        unit_of_measure = uom_map.get(account.meter_type, UnitOfMeasure.KWH)
        timezone = self.utility.timezone()
        start_date = align_datetime(start_date, read_resolution, snap_up=False, tz=timezone)
        end_date = align_datetime(end_date, read_resolution, snap_up=True, tz=timezone)

        return await self._async_fetch_combined_reads(account, read_resolution, start_date, end_date, unit_of_measure)

    async def async_fetch_latest_meter_data(
        self, account: Account, read_resolution: ReadResolution = ReadResolution.HOURLY, lookback_hours: int = 6
    ) -> list[Reading]:
        """Fetch the most recent data points for a specific account.

        Args:
            account: The Account instance to fetch readings for.
            read_resolution: The resolution of the readings (defaults to hourly).
            lookback_hours: Number of hours to look back (defaults to 6).

        Returns:
            A list of the latest Reading objects.

        """
        end = datetime.now(UTC)
        start = end - timedelta(hours=lookback_hours)
        return await self.async_fetch_meter_data(account, read_resolution, start, end)
