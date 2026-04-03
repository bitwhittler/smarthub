"""Exceptions for the SmartHub API."""


class SmartHubError(Exception):
    """Base exception for SmartHub errors."""


class SmartHubAuthError(SmartHubError):
    """Raised when authentication fails."""


class SmartHubApiError(SmartHubError):
    """Raised when an API request fails."""


class SmartHubTimeoutError(SmartHubError):
    """Raised when the SmartHub server backend times out during a polling cycle."""


class SmartHubMfaChallenge(SmartHubAuthError):
    """Raised when Multi-Factor Authentication is required but a TOTP secret was not provided."""
