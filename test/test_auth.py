"""Tests the optional shared-password gate (app/auth.py and the middleware in app/main.py).

No keys, database or network needed: only pages that never touch a service are requested.

Run with: uv run test/test_auth.py
"""

import base64
import os
import sys
from pathlib import Path

os.environ["OBSERVABILITY_PROVIDER"] = "none"
os.environ["APP_PASSWORD"] = "s3cret-Pässword"   # non-ASCII on purpose
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import password_ok  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = os.environ["APP_PASSWORD"]
failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    failures += 0 if ok else 1


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def main() -> int:
    # ---- the check itself ----
    good = basic("anyone", PASSWORD)["Authorization"]
    check("correct password is accepted", password_ok(good, PASSWORD))
    check("username is ignored", password_ok(basic("", PASSWORD)["Authorization"], PASSWORD))
    check("wrong password is rejected", not password_ok(basic("u", "nope")["Authorization"], PASSWORD))
    check("a prefix of the password is rejected", not password_ok(basic("u", PASSWORD[:-1])["Authorization"], PASSWORD))
    check("the password plus extra is rejected", not password_ok(basic("u", PASSWORD + "x")["Authorization"], PASSWORD))
    check("missing header is rejected", not password_ok(None, PASSWORD))
    check("wrong scheme is rejected", not password_ok("Bearer abc", PASSWORD))
    check("garbage base64 is rejected", not password_ok("Basic !!!not-base64!!!", PASSWORD))
    check("no colon is rejected", not password_ok("Basic " + base64.b64encode(b"nocolon").decode(), PASSWORD))
    check("password containing a colon works",
          password_ok(basic("u", "a:b:c")["Authorization"], "a:b:c"))
    check("scheme word is case-insensitive", password_ok(good.replace("Basic", "basic"), PASSWORD))

    # ---- the app with the password set ----
    get_settings.cache_clear()
    http = TestClient(app)
    r = http.get("/")
    check("page without a password: 401", r.status_code == 401)
    check("401 asks the browser for a login", "Basic" in r.headers.get("www-authenticate", ""))
    check("page with a wrong password: 401", http.get("/", headers=basic("u", "wrong")).status_code == 401)
    r = http.get("/", headers=basic("u", PASSWORD))
    check("page with the right password: 200", r.status_code == 200 and "Umbrella" in r.text)
    check("static JS is protected", http.get("/static/js/app.js").status_code == 401)
    check("static JS with the password: 200",
          http.get("/static/js/app.js", headers=basic("u", PASSWORD)).status_code == 200)
    check("API is protected (no password)", http.post("/api/chat", json={"question": "hello there"}).status_code == 401)
    check("API docs are protected", http.get("/api/docs").status_code == 401)
    check("approvals endpoint is protected", http.get("/api/approvals").status_code == 401)
    check("health/live stays open for the platform's health check", http.get("/health/live").status_code == 200)
    check("health/ready is protected", http.get("/health/ready").status_code == 401)

    # ---- the app with no password set ----
    os.environ["APP_PASSWORD"] = ""
    get_settings.cache_clear()
    check("no password configured: page is open", http.get("/").status_code == 200)
    check("no password configured: static is open", http.get("/static/css/styles.css").status_code == 200)

    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
