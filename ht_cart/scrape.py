"""Open the Harris Teeter cart in Camoufox (signing in if needed), capture the
JSON it loads, and write the aisle-sorted result to data/cart.json.

Every run saves its raw captures to data/captures/<timestamp>/ (responses,
screenshot, page HTML) so the parser can be fitted to the real data shape.
"""

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import parse
from .browser import Browser

log = logging.getLogger(__name__)

BASE_URL = os.environ.get("HT_BASE_URL", "https://www.harristeeter.com").rstrip("/")
DATA_DIR = Path(os.environ.get("HT_DATA_DIR", "data"))
KEEP_CAPTURES = 5

# Hosts whose JSON we keep. Kroger banners serve APIs from their own domain;
# login runs on login.kroger.com.
JSON_HOSTS = ("harristeeter.com", "kroger.com")

EMAIL_SEL = (
    "input[type=email], input[name*=email i], input[id*=email i], "
    "input[autocomplete=username]"
)
PASSWORD_SEL = "input[type=password]"
SUBMIT_SEL = "button[type=submit], input[type=submit]"
OTP_SEL = (
    "input[autocomplete=one-time-code], input[name*=code i], "
    "input[id*=otp i], input[id*=code i]"
)


class ScrapeError(Exception):
    """A run that stopped for a known reason. The message is shown on the page."""


def _creds():
    email, password = os.environ.get("HT_EMAIL"), os.environ.get("HT_PASSWORD")
    if not email or not password:
        raise ScrapeError("HT_EMAIL / HT_PASSWORD are not set in the env file.")
    return email, password


def _visible(page, sel, timeout=0):
    loc = page.locator(sel).first
    try:
        loc.wait_for(state="visible", timeout=timeout or 1)
        return loc
    except Exception:  # noqa: BLE001 - playwright TimeoutError
        return None


def _on_cart(page):
    return urlparse(page.url).path.rstrip("/").endswith("/cart")


def _sign_in(page, run_dir):
    """Fill whatever sign-in form we land on. Handles a one-page form and the
    email -> Continue -> password two-step. Stops on a verification-code
    prompt, since that needs a human."""
    email, password = _creds()
    field = _visible(page, EMAIL_SEL, timeout=20000)
    if not field:
        raise ScrapeError("Sign-in page did not show an email field.")
    field.click()
    field.press_sequentially(email, delay=70)
    pw = _visible(page, PASSWORD_SEL, timeout=1500)
    if not pw:
        page.locator(SUBMIT_SEL).first.click()
        pw = _visible(page, PASSWORD_SEL, timeout=15000)
    if not pw:
        if _visible(page, OTP_SEL, timeout=1000):
            raise ScrapeError("Sign-in asked for a verification code.")
        raise ScrapeError("Sign-in page did not show a password field.")
    pw.click()
    pw.press_sequentially(password, delay=70)
    time.sleep(0.6)
    page.locator(SUBMIT_SEL).first.click()

    deadline = time.time() + 45
    while time.time() < deadline:
        if _on_cart(page):
            return
        if _visible(page, OTP_SEL, timeout=500):
            page.screenshot(path=str(run_dir / "signin-code.png"))
            raise ScrapeError("Sign-in asked for a verification code (not handled yet).")
        time.sleep(1)
    page.screenshot(path=str(run_dir / "signin-failed.png"))
    raise ScrapeError(f"Sign-in did not reach the cart (stuck at {page.url}).")


def _save_captures(responses, run_dir):
    """Read every JSON body we saw. Bodies are pulled here, after the page
    settles, rather than inside the response callback (blocking calls inside
    sync-API callbacks can deadlock)."""
    captures = []
    for i, r in enumerate(responses):
        try:
            if not any(h in (urlparse(r.url).hostname or "") for h in JSON_HOSTS):
                continue
            if "json" not in (r.headers.get("content-type") or ""):
                continue
            body = r.json()
        except Exception:  # noqa: BLE001 - body gone, not JSON, redirect...
            continue
        cap = {"url": r.url, "status": r.status, "body": body}
        captures.append(cap)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", urlparse(r.url).path)[:80].strip("-")
        (run_dir / f"{i:03d}-{slug}.json").write_text(json.dumps(cap, indent=1))
    return captures


def _prune_captures(root):
    runs = sorted(p for p in root.iterdir() if p.is_dir())
    for old in runs[:-KEEP_CAPTURES]:
        shutil.rmtree(old, ignore_errors=True)


def run():
    """One full refresh. Returns the result dict written to data/cart.json."""
    _creds()
    cap_root = DATA_DIR / "captures"
    run_dir = cap_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    responses = []

    with Browser(DATA_DIR / "profile") as page:
        # A lambda, not responses.append: Playwright tags the handler with an
        # attribute, which builtin methods can't take.
        page.on("response", lambda r: responses.append(r))
        # Kroger sites bounce /signin straight to redirectUrl when the
        # session is still good, so this one URL covers both cases.
        page.goto(f"{BASE_URL}/signin?redirectUrl=/cart", wait_until="domcontentloaded")
        time.sleep(3)
        if not _on_cart(page):
            _sign_in(page, run_dir)

        # Only keep what the cart page itself loaded.
        responses.clear()
        page.goto(f"{BASE_URL}/cart", wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:  # noqa: BLE001 - analytics keep the network busy
            pass
        # Scroll so lazily loaded item cards fetch their product details.
        for _ in range(8):
            page.mouse.wheel(0, 1400)
            time.sleep(0.8)
        time.sleep(2)

        page.screenshot(path=str(run_dir / "cart.png"), full_page=True)
        (run_dir / "cart.html").write_text(page.content())
        captures = _save_captures(responses, run_dir)

    result = parse.build(captures)
    result["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    result["capture_dir"] = run_dir.name
    result["captured_responses"] = len(captures)
    (DATA_DIR / "cart.json").write_text(json.dumps(result, indent=1))
    _prune_captures(cap_root)
    log.info(
        "cart refreshed: %d items, %d responses, warnings=%s",
        len(result["items"]), len(captures), result["warnings"],
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
