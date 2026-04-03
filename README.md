# SmartHub

[![Run Pytest](https://github.com/bitwhittler/smarthub/actions/workflows/pytest.yml/badge.svg)](https://github.com/bitwhittler/smarthub/actions/workflows/pytest.yml)
[![Pre-commit](https://github.com/bitwhittler/smarthub/actions/workflows/pre-commit.yml/badge.svg)](https://github.com/bitwhittler/smarthub/actions/workflows/pre-commit.yml)

A Python library for gathering historical usage and cost data from electric
cooperatives that use the [SmartHub](https://www.nisc.coop/blog/beyond-the-bill-the-power-of-smarthub/)
platform.

This library is used by the SmartHub Home Assistant integration.(Coming Soon)

## Supported Utilities

| Provider                  | Provider ID |
| ------------------------- | ----------- |
| CORE Electric Cooperative | `core`      |

> Adding your cooperative is simple:
>
> 1. Create a new file under `src/smarthub/utilities/` and subclass `UtilityBase`.
> 2. Import and expose your subclass in `src/smarthub/utilities/__init__.py`.
> See [core.py](src/smarthub/utilities/core.py) and
> [__init__.py](src/smarthub/utilities/__init__.py) as examples.

## Installation

```sh
pip install smarthub
```

## Usage

### As a Library

```python
import asyncio
from smarthub import SmartHub, ReadResolution

async def main():
    async with SmartHub("core", "user@example.com", "password") as client:
        await client.async_login()
        accounts = await client.async_fetch_accounts()

        for account in accounts:
            readings = await client.async_fetch_latest_meter_data(
                account, ReadResolution.HOURLY, lookback_hours=24
            )
            for r in readings:
                print(f"{r.start_time} — {r.usage} {r.unit_of_measure}")

asyncio.run(main())
```

### As a CLI

The CLI reads credentials from environment variables (`SMARTHUB_USERNAME`,
`SMARTHUB_PASSWORD`, `SMARTHUB_TOTP_SECRET`) or prompts for them securely if
not provided.

```sh
# Set credentials in env
export SMARTHUB_USERNAME="user@example.com"
export SMARTHUB_PASSWORD="mypassword"
export SMARTHUB_TOTP_SECRET="mysecret"  # Optional, if MFA/TOTP is required

# After pip install
smarthub --provider core -vv

# Or via python -m
python -m smarthub --provider core

# Fetch latest 24 hours of data
smarthub --provider core --latest 24

# Export to CSV
smarthub --provider core --csv output.csv
```

## Development

```sh
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
python -m pip install --upgrade pip
python -m pip install -e ".[test]"

# Run pre-commit
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files

# Run tests
pytest

# Run tests with coverage
pytest --cov --cov-report=term-missing

# Build package
python -m pip install build
python -m build
```
