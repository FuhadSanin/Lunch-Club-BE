"""Is the order bot ready to run? Each prerequisite is checked and, if it fails, comes
with the command that fixes it. Shown to the host on the Place order screen."""

import importlib.util
from pathlib import Path
from urllib.parse import urlparse

import httpx
from django.conf import settings

CHROME = '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"'


def cdp_url() -> str:
    return settings.SWIGGY_BOT_CDP_URL.strip()


def chrome_command() -> str:
    port = urlparse(cdp_url() or "http://127.0.0.1:9222").port or 9222
    return (
        f"{CHROME} --remote-debugging-port={port} "
        '--user-data-dir="$HOME/chrome-swiggy-debug"'
    )


def commands() -> list[dict]:
    """Every command the host may need, in the order they are used."""
    url = cdp_url() or "http://127.0.0.1:9222"
    return [
        {
            "title": "1. Quit Chrome completely",
            "why": "Chrome only opens the debug port when it starts fresh with the "
            "flag.",
            "command": "osascript -e 'quit app \"Google Chrome\"'",
        },
        {
            "title": "2. Start Chrome with the debug port",
            "why": "The bot drives this browser. It opens its own window and profile.",
            "command": chrome_command(),
        },
        {
            "title": "3. Check Chrome is listening",
            "why": "Should print JSON that names the browser. No output means step 2 "
            "didn't take.",
            "command": f"curl {url}/json/version",
        },
        {
            "title": "4. Run the API on your Mac",
            "why": "A Docker API can't reach your Chrome, and has no Playwright.",
            "command": "make bot-api",
        },
        {
            "title": "Go back to the Docker API",
            "why": "For everyday use without the bot.",
            "command": "docker compose up -d api",
        },
        {
            "title": "Watch the API log",
            "why": "Shows each bot step and any error the page can't.",
            "command": "docker compose logs -f api",
            "note": "Using make bot-api? The log is in that terminal instead.",
        },
    ]


def chrome_version() -> str | None:
    """The browser listening on the debug port, or None if nothing answers."""
    try:
        reply = httpx.get(f"{cdp_url()}/json/version", timeout=2)
        return reply.json().get("Browser") or "unknown browser"
    except (httpx.HTTPError, ValueError):
        return None


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


def checks() -> list[dict]:
    playwright = importlib.util.find_spec("playwright") is not None
    provider_fix = (
        "Set ORDER_PROVIDER=apps.ordering.providers.swiggy_web.SwiggyWebProvider "
        "in .env, then restart the API."
    )
    result = [
        check(
            "provider",
            "Order bot is switched on",
            "The API only runs the bot when ORDER_PROVIDER points at it.",
            ("swiggy_web" in settings.ORDER_PROVIDER, settings.ORDER_PROVIDER),
            provider_fix,
        ),
        check(
            "runtime",
            "API runs on your Mac, not in Docker",
            "Your Chrome is on your Mac; a container can't reach it.",
            (not in_docker(), "Docker" if in_docker() else "This machine"),
            "make bot-api",
        ),
        check(
            "playwright",
            "Playwright is installed",
            "It is the library that clicks around swiggy.com.",
            (playwright, "Found" if playwright else "Not installed in this API"),
            "uv sync --group bot",
        ),
    ]
    if cdp_url():
        browser = chrome_version()
        result.append(
            check(
                "chrome",
                f"Chrome is listening on {cdp_url()}",
                "The bot attaches to this browser to use your logged-in Swiggy.",
                (
                    browser is not None,
                    f"Connected to {browser}"
                    if browser
                    else f"Nothing is listening on {cdp_url()}",
                ),
                chrome_command(),
            )
        )
    return result


def check(key: str, label: str, why: str, outcome: tuple[bool, str], fix: str) -> dict:
    ok, detail = outcome
    return {
        "key": key,
        "label": label,
        "why": why,
        "ok": ok,
        "detail": detail,
        "fix": "" if ok else fix,
    }
