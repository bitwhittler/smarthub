"""Tests for the SmartHub CLI entrypoint."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

from pathlib import Path

import pytest

from smarthub.__main__ import main, main_sync, parse_args
from smarthub.models import ReadResolution


def test_parse_args_provider_required() -> None:
    """Verify that omitting provider raises SystemExit."""
    with patch("sys.argv", ["smarthub-cli"]), pytest.raises(SystemExit):
        parse_args()


@pytest.mark.asyncio
async def test_main_success() -> None:
    """Verify that main runs and logs in successfully."""
    mock_client = MagicMock()
    mock_client.async_login = AsyncMock()
    mock_client.async_fetch_accounts = AsyncMock(return_value=[])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()

    args = argparse.Namespace(
        provider="core",
        username="user",
        password="password",
        totp_secret=None,
        read_resolution=ReadResolution.HOURLY,
        start_date=None,
        end_date=None,
        latest=None,
        usage_only=False,
        csv=None,
        verbose=0,
    )

    with patch("smarthub.__main__.SmartHub", return_value=mock_client):
        await main(args, "user", "password", None)
        mock_client.async_login.assert_called_once()
        mock_client.async_fetch_accounts.assert_called_once()


def test_main_sync_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that credentials are prompted if not provided via environment or CLI."""
    inputs = iter(["prompted-username", "prompted-password", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    with (
        patch("getpass.getpass", lambda _: next(inputs)),
        patch("sys.argv", ["smarthub-cli", "--provider", "core"]),
        patch("smarthub.__main__.main", new_callable=AsyncMock) as mock_main,
    ):
        main_sync()
        # Verify that main was called with the prompted credentials
        mock_main.assert_called_once()
        _args_called, username_called, password_called, totp_secret_called = mock_main.call_args[0]
        assert username_called == "prompted-username"
        assert password_called == "prompted-password"
        assert totp_secret_called is None


def test_save_to_csv_permissions(tmp_path: Path) -> None:
    """Verify that CSV output files are written with secure permissions (0o600)."""
    import os
    from datetime import datetime, UTC
    from smarthub.__main__ import save_to_csv
    from smarthub.models import Reading, MeterType, FlowDirection, UnitOfMeasure
    from pathlib import Path

    csv_file = Path(tmp_path) / "test.csv"
    data = [
        Reading(
            start_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
            usage=1.5,
            unit_of_measure=UnitOfMeasure.KWH,
            meter_id="12345",
            meter_type=MeterType.ELEC,
            flow_direction=FlowDirection.FORWARD,
            cost=0.25,
        )
    ]
    save_to_csv(str(csv_file), data, usage_only=False)

    assert csv_file.exists()
    mode = os.stat(csv_file).st_mode & 0o777
    assert mode == 0o600


def test_select_utility_invalid() -> None:
    """Verify select_utility raises ValueError for invalid provider name."""
    from smarthub.utilities import select_utility

    with pytest.raises(ValueError, match="Utility provider 'invalid' not found"):
        select_utility("invalid")


@pytest.mark.asyncio
async def test_async_process_account_latest() -> None:
    """Verify async_process_account processes accounts with latest flag."""
    from smarthub.__main__ import async_process_account
    from smarthub.models import Account

    mock_client = MagicMock()
    mock_client.async_fetch_latest_meter_data = AsyncMock(return_value=[])

    acc = Account(
        customer_id="999000",
        customer_name="JOHN DOE",
        account_numbers=["111222333"],
        primary_account_number="111222333",
        service_location_number="SL-1",
    )

    args = argparse.Namespace(
        latest=6,
        read_resolution=ReadResolution.HOURLY,
        csv=None,
        usage_only=False,
    )

    await async_process_account(mock_client, acc, args)
    mock_client.async_fetch_latest_meter_data.assert_called_once_with(
        acc, read_resolution=ReadResolution.HOURLY, lookback_hours=6
    )
