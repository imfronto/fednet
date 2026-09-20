class PiholeError(Exception):
    """Base class for all Pi-hole integration errors."""


class PiholeConnectionError(PiholeError):
    """Pi-hole is unreachable (network failure, timeout, DNS failure to reach it).
    Callers must treat this as 'unknown', never as evidence of anything about
    the domains being processed."""


class PiholeAuthError(PiholeError):
    """Authentication failed or the session expired and could not be renewed."""


class PiholeAPIError(PiholeError):
    """Pi-hole responded but rejected the request (4xx/5xx)."""

    def __init__(self, status_code: int, message: str, key: str | None = None):
        super().__init__(f"Pi-hole API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.key = key


class UnsafeOperationRefused(PiholeError):
    """Raised when a caller asks the client to delete/modify a domain-list
    entry that this service did not itself create. This is a hard,
    non-bypassable guard — see requirement #7/#22 ('never modify manually
    managed Pi-hole entries')."""
