"""Main entry point for the SmartHub CLI utility."""

import argparse
import asyncio
import csv
import getpass
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

from smarthub.exceptions import SmartHubAuthError, SmartHubMfaChallenge
from smarthub.models import Account, Reading, ReadResolution
from smarthub.smarthub import SmartHub

_LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="SmartHub CLI Testing Utility")
    parser.add_argument("--provider", required=True, help="SmartHub provider ID (e.g. core)")
    parser.add_argument("--username", required=False, help="SmartHub username (or set SMARTHUB_USERNAME)")
    parser.add_argument(
        "--read_resolution",
        help="How to aggregate historical data. Defaults to HOURLY",
        choices=list(ReadResolution),
        type=ReadResolution,
        default=ReadResolution.HOURLY,
    )
    parser.add_argument(
        "--start_date",
        help="Start datetime for historical data (ISO format). Defaults to 7 days ago",
        type=datetime.fromisoformat,
        default=datetime.now(UTC) - timedelta(days=7),
    )
    parser.add_argument(
        "--end_date",
        help="End datetime for historical data (ISO format). Defaults to now",
        type=datetime.fromisoformat,
        default=datetime.now(UTC),
    )
    parser.add_argument(
        "--latest",
        type=int,
        help="Fetch latest N hours of data (overrides --start_date/--end_date)",
    )
    parser.add_argument(
        "--usage_only",
        help="If true will output usage only, not cost",
        action="store_true",
    )
    parser.add_argument(
        "--csv",
        help="CSV file to store data",
    )
    parser.add_argument("-v", "--verbose", help="Enable verbose logging", action="count", default=0)

    return parser.parse_args()


def setup_logging(verbose: int) -> None:
    """Configure logging based on verbosity level."""
    log_level = logging.INFO
    if verbose >= 1:
        log_level = logging.DEBUG

    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")


def display_account(acc: Account, is_csv: bool) -> None:
    """Display account information."""
    if is_csv:
        return

    _LOGGER.info("")
    _LOGGER.info("Customer Name: %s (ID: %s)", acc.customer_name, acc.customer_id)
    _LOGGER.info("  Account: %s", acc.primary_account_number)
    _LOGGER.info("  Service Location: %s", acc.service_location_number)
    _LOGGER.info("-" * 30)


def display_meter_reads(data: list[Reading], usage_only: bool) -> None:
    """Display meter reading data to the console."""
    for read in data:
        msg = (
            f"  [{read.meter_id}] ({read.meter_type}) "
            f"{read.start_time} to {read.end_time}: "
            f"{read.usage} {read.unit_of_measure} ({read.flow_direction})"
        )
        if not usage_only:
            msg += f" (${read.cost})"
        _LOGGER.info(msg)


def save_to_csv(filepath: str, data: list[Reading], usage_only: bool) -> None:
    """Save meter readings to a CSV file."""
    headers = [
        "start_time",
        "end_time",
        "meter_id",
        "meter_type",
        "usage",
        "unit_of_measure",
        "flow_direction",
    ]
    if not usage_only:
        headers.append("cost")

    try:

        def secure_opener(path: str, flags: int) -> int:
            return os.open(path, flags, 0o600)

        with open(filepath, "w", newline="", encoding="utf-8", opener=secure_opener) as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(headers)
            for read in data:
                row = [
                    read.start_time,
                    read.end_time,
                    read.meter_id,
                    read.meter_type,
                    read.usage,
                    read.unit_of_measure,
                    read.flow_direction,
                ]
                if not usage_only:
                    row.append(read.cost)
                writer.writerow(row)
    except OSError:
        _LOGGER.exception("Failed to write CSV file: %s", filepath)
        return
    _LOGGER.info("Data saved to %s", filepath)


async def async_process_account(client: SmartHub, acc: Account, args: argparse.Namespace) -> None:
    """Fetch and display data for a single account."""
    display_account(acc, bool(args.csv))

    if not acc.service_location_number:
        return

    _LOGGER.info("Fetching %s data...", args.read_resolution.value)

    try:
        if args.latest:
            data = await client.async_fetch_latest_meter_data(
                acc, read_resolution=args.read_resolution, lookback_hours=args.latest
            )
        else:
            data = await client.async_fetch_meter_data(acc, args.read_resolution, args.start_date, args.end_date)

        if args.csv:
            await asyncio.to_thread(save_to_csv, args.csv, data, args.usage_only)
        else:
            display_meter_reads(data, args.usage_only)

    except Exception:
        _LOGGER.exception("  Failed to fetch data for account %s", acc.primary_account_number)


async def main(args: argparse.Namespace, username: str, password: str, totp_secret: str | None) -> None:
    """Execute the main CLI entry point."""
    async with SmartHub(args.provider, username, password, totp_secret) as client:
        _LOGGER.info("Logging in to %s as %s...", args.provider, username)
        try:
            await client.async_login()
            _LOGGER.info("Login successful.")
        except SmartHubMfaChallenge as err:
            _LOGGER.error("Multi-Factor Authentication required: %s", err)  # noqa: TRY400
            return
        except SmartHubAuthError as err:
            _LOGGER.error("Login failed: %s", err)  # noqa: TRY400
            return
        except Exception:
            _LOGGER.exception("Unexpected error during login")
            return

        _LOGGER.info("Fetching accounts...")
        try:
            accounts = await client.async_fetch_accounts()
        except Exception:
            _LOGGER.exception("Failed to fetch accounts")
            return

        for acc in accounts:
            await async_process_account(client, acc, args)

            if not args.csv:
                _LOGGER.info("=" * 30)


def main_sync() -> None:
    """Run the async main entry point synchronously for console_scripts."""
    args = parse_args()
    setup_logging(args.verbose)

    username = args.username or os.environ.get("SMARTHUB_USERNAME")
    if not username:
        username = input("Username: ").strip()

    password = os.environ.get("SMARTHUB_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")

    totp_secret = os.environ.get("SMARTHUB_TOTP_SECRET")
    if not totp_secret:
        # Prompt option for TOTP secret
        totp_secret = getpass.getpass("TOTP Secret (optional, press Enter to skip): ").strip() or None

    try:
        asyncio.run(main(args, username, password, totp_secret))
    except KeyboardInterrupt:
        pass
    except Exception:
        _LOGGER.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main_sync()
