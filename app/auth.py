"""Optional shared-password gate for the whole app (HTTP Basic authentication).

Set APP_PASSWORD in .env (or in the hosting dashboard) and the browser shows its standard
login box before anything is served. Any username works; only the password is checked.
Leave APP_PASSWORD empty and the app is open, as in local development.

This is a single shared password, not user accounts. It keeps casual visitors out of a public
URL. Use a long random password, and only over HTTPS (Render provides it).
"""
import base64
import hmac

# Paths served without a password. Render's health check cannot send credentials.
OPEN_PATHS = frozenset({"/health/live"})


def password_ok(authorization_header: str | None, password: str) -> bool:
    """True if the header is `Basic <base64(user:password)>` with the right password."""
    if not authorization_header or not authorization_header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(authorization_header[6:].strip(), validate=True).decode("utf-8")
    except Exception:  # not valid base64 / not UTF-8
        return False
    _user, separator, given = decoded.partition(":")
    if not separator:
        return False
    # constant-time comparison, so response time does not reveal how much matched
    return hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8"))
