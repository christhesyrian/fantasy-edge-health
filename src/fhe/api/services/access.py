"""One shared password in front of the whole API.

Deliberately not user accounts. This exists so that a handful of named friends
can use a deployed instance, and an account system nobody asked for would be
more surface to get wrong than the thing it protects.

The session token
-----------------
Signing is keyed on the password itself, so there is no second secret to
configure and no server-side session store to keep. A token is
``expiry.signature`` where the signature is an HMAC over the expiry under a key
derived from the password. That buys three properties worth having:

* it is stateless, so a restart mid-draft does not log everybody out;
* it expires, so a cookie left on a borrowed laptop stops working; and
* **changing the password invalidates every existing session**, because the key
  that signed them is gone. That is the one operation you actually want after a
  password gets passed around a group chat.

The token is never the password and cannot be reversed into it.

What is deliberately not defended against
-----------------------------------------
A shared password is shared. Anyone holding it can hand it on, and this cannot
tell two holders apart. It raises the bar from "anyone with the URL" to "anyone
you told", which is the whole requirement.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Final

from fhe.config import Settings

# Cookie name. The `__Host-` prefix is deliberately not used: it forbids a
# Domain attribute and requires Path=/, which would break an API served under a
# path prefix behind a proxy.
COOKIE_NAME: Final = "fhe_session"

# Domain separation for the key derivation, so this HMAC key could never
# collide with another use of the same password elsewhere.
_KEY_INFO: Final = b"fhe.access.session.v1"

# Bounds a malformed cookie cannot exceed, so a hostile client cannot make the
# parser work hard before it fails.
_MAX_TOKEN_BYTES: Final = 256


def _signing_key(password: str) -> bytes:
    """Derive the HMAC key from the shared password."""
    return hashlib.sha256(_KEY_INFO + password.encode("utf-8")).digest()


def _sign(password: str, payload: str) -> str:
    """Signature for one payload under the password's key."""
    return hmac.new(_signing_key(password), payload.encode("ascii"), hashlib.sha256).hexdigest()


def issue_token(settings: Settings, *, now: float | None = None) -> str:
    """Mint a session token valid for the configured window."""
    expires_at = int(
        (now if now is not None else time.time()) + settings.access_session_hours * 3600
    )
    payload = str(expires_at)
    return f"{payload}.{_sign(settings.access_password, payload)}"


def token_is_valid(settings: Settings, token: str | None, *, now: float | None = None) -> bool:
    """Whether a token was signed by the current password and has not expired."""
    if not token or len(token) > _MAX_TOKEN_BYTES:
        return False
    payload, _, signature = token.partition(".")
    if not payload or not signature:
        return False
    # Compared before parsing the expiry: an unsigned payload should never
    # influence anything, including which branch is taken.
    if not hmac.compare_digest(_sign(settings.access_password, payload), signature):
        return False
    try:
        expires_at = int(payload)
    except ValueError:
        return False
    return (now if now is not None else time.time()) < expires_at


def password_matches(settings: Settings, candidate: str) -> bool:
    """Whether a submitted password is the configured one.

    Compared in constant time. A plain ``==`` on a secret leaks its length and,
    given enough attempts, its contents.
    """
    return hmac.compare_digest(settings.access_password, candidate)


@dataclass
class AttemptLimiter:
    """Refuses an address that keeps guessing.

    A shared password is guessable by anyone patient, and an unthrottled login
    endpoint makes patience free. This makes it expensive without needing a
    store: the state is a dictionary in one process.

    That process-local scope is a real limit and is reported as a degradation
    rather than hidden - with several API workers an attacker gets the allowance
    once per worker. It is proportionate to what this protects, and Redis-backed
    counting is the answer if this ever guards something that matters more.
    """

    max_attempts: int
    lockout_seconds: float
    # address -> (failures, when the count resets)
    _failures: dict[str, tuple[int, float]] = field(default_factory=dict)

    def is_locked(self, address: str, *, now: float | None = None) -> bool:
        """Whether this address must wait before trying again."""
        moment = now if now is not None else time.time()
        record = self._failures.get(address)
        if record is None:
            return False
        count, expires_at = record
        if moment >= expires_at:
            del self._failures[address]
            return False
        return count >= self.max_attempts

    def record_failure(self, address: str, *, now: float | None = None) -> None:
        """Note a wrong password from this address."""
        moment = now if now is not None else time.time()
        count, expires_at = self._failures.get(address, (0, 0.0))
        if moment >= expires_at:
            count = 0
        self._failures[address] = (count + 1, moment + self.lockout_seconds)

    def clear(self, address: str) -> None:
        """Forget an address's failures, after it gets the password right."""
        self._failures.pop(address, None)

    def retry_after_seconds(self, address: str, *, now: float | None = None) -> int:
        """Seconds until this address may try again."""
        record = self._failures.get(address)
        if record is None:
            return 0
        moment = now if now is not None else time.time()
        return max(0, int(record[1] - moment))
