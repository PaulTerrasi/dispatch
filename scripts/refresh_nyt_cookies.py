#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "browser-cookie3>=0.20",
#     "boto3>=1.34",
# ]
# ///
"""Refresh NYT subscription cookies in AWS SSM.

Reads NYT-scoped cookies from a logged-in local browser profile, serializes
them as a `Cookie` header value, and writes them to the SSM SecureString
parameter `/morning-digest/nyt-cookies` in us-east-1. The dispatch curation
agent reads this parameter at cold start and injects it on requests to
nytimes.com to bypass the paywall with the user's subscription.

NYT cookies auto-extend on every visit, so running this once a day from a
machine where you stay logged in keeps the parameter valid indefinitely.

Usage:
    uv run scripts/refresh_nyt_cookies.py
    uv run scripts/refresh_nyt_cookies.py --browser firefox
    uv run scripts/refresh_nyt_cookies.py --dry-run

Install on a schedule:
    macOS (launchd):  see scripts/com.morningdigest.nyt-cookies.plist
    Linux  (cron):    crontab -e
                      0 7 * * * cd /path/to/dispatch && uv run scripts/refresh_nyt_cookies.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from typing import Any

import boto3
import browser_cookie3

SSM_PARAM = "/morning-digest/nyt-cookies"
REGION = "us-east-1"

# Order is the auto-detect priority when --browser isn't given.
BROWSER_LOADERS: dict[str, Callable[..., Any]] = {
    "chrome": browser_cookie3.chrome,
    "firefox": browser_cookie3.firefox,
    "safari": browser_cookie3.safari,
    "edge": browser_cookie3.edge,
    "brave": browser_cookie3.brave,
}

log = logging.getLogger("refresh-nyt-cookies")


def extract_nyt_cookies(browser: str | None) -> str:
    """Return cookies as a `name=value; name=value` Cookie header value.

    With `browser` set, tries only that one and raises on failure. Without it,
    walks the BROWSER_LOADERS preference order and returns the first hit.
    """
    candidates = [browser] if browser else list(BROWSER_LOADERS)
    last_err: Exception | None = None
    for name in candidates:
        loader = BROWSER_LOADERS.get(name)
        if loader is None:
            raise SystemExit(f"unknown browser: {name}")
        try:
            jar = loader(domain_name="nytimes.com")
        except Exception as e:
            last_err = e
            log.debug("browser %s unavailable: %s", name, e)
            continue
        # http.cookiejar.Cookie.value is Optional[str]; drop valueless cookies
        # so we don't serialize them as the literal string "name=None".
        pairs = [
            (c.name, c.value)
            for c in jar
            if "nytimes.com" in (c.domain or "") and c.value is not None
        ]
        if pairs:
            log.info("found %d NYT cookies in %s", len(pairs), name)
            return "; ".join(f"{n}={v}" for n, v in pairs)
        log.debug("no NYT cookies in %s profile", name)
    if last_err:
        raise SystemExit(f"could not read cookies from any browser: {last_err}")
    raise SystemExit(
        "no NYT cookies found — log in at https://www.nytimes.com in your browser first"
    )


def current_ssm_value(ssm: Any) -> str | None:
    try:
        result = ssm.get_parameter(Name=SSM_PARAM, WithDecryption=True)
        return str(result["Parameter"]["Value"])
    except ssm.exceptions.ParameterNotFound:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument(
        "--browser",
        choices=sorted(BROWSER_LOADERS),
        help="force a specific browser (default: auto-detect)",
    )
    p.add_argument("--dry-run", action="store_true", help="extract cookies but do not write SSM")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    cookies = extract_nyt_cookies(args.browser)

    if args.dry_run:
        log.info("dry-run: would write %d chars to %s", len(cookies), SSM_PARAM)
        return 0

    ssm = boto3.client("ssm", region_name=REGION)
    if current_ssm_value(ssm) == cookies:
        log.info("SSM %s already current; skipping write", SSM_PARAM)
        return 0

    ssm.put_parameter(Name=SSM_PARAM, Value=cookies, Type="SecureString", Overwrite=True)
    log.info("wrote %d chars to SSM %s", len(cookies), SSM_PARAM)
    return 0


if __name__ == "__main__":
    sys.exit(main())
