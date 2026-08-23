#!/usr/bin/env python3
"""Share newly published radar reports to a LinkedIn Company Page.

Design notes:
  - Uses only the refresh token as a durable secret; a fresh access token
    is minted in-memory on each run (LinkedIn refresh tokens last ~365d).
  - Posted URLs are tracked in scripts/linkedin_state.json so retries or
    re-runs never double-post.
  - Every failure path degrades gracefully: the radar pipeline must keep
    working even when LinkedIn is misconfigured, rate-limited, or down.
"""
import json
import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger("linkedin")

API_BASE = "https://api.linkedin.com"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SITE_BASE = "https://www.cyberpent.cc.cd"
STATE_FILE = Path(__file__).resolve().parent / "linkedin_state.json"
LINKEDIN_VERSION = "202408"
MAX_LEN = 2800

REQUIRED_VARS = (
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_REFRESH_TOKEN",
    "LINKEDIN_ORG_URN",
)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def configured() -> bool:
    return all(_env(v) for v in REQUIRED_VARS)


def _load_posted() -> set:
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return set()


def _save_posted(posted: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(posted), indent=1), encoding="utf-8")


def _access_token() -> str:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": _env("LINKEDIN_REFRESH_TOKEN"),
        "client_id": _env("LINKEDIN_CLIENT_ID"),
        "client_secret": _env("LINKEDIN_CLIENT_SECRET"),
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rotated = data.get("refresh_token")
    if rotated and rotated != _env("LINKEDIN_REFRESH_TOKEN"):
        log.warning(
            "LinkedIn issued a NEW refresh token - update the "
            "LINKEDIN_REFRESH_TOKEN secret soon (old one may expire)."
        )
    return data["access_token"]


def permalink_for(stem: str) -> str:
    """'2026-08-22-my-slug-abc12345.md' base -> live site permalink."""
    name = stem[:-3] if stem.endswith(".md") else stem
    parts = name.split("-", 3)
    if len(parts) != 4:
        raise ValueError(f"unexpected post stem: {name}")
    year, month, day, slug = parts
    return f"{SITE_BASE}/radar/{year}/{month}/{day}/{slug}.html"


def compose(entry: dict) -> str:
    cves = entry.get("cves") or []
    lines = [
        f"[{entry['severity'].upper()}] {entry['title']}",
        "",
        f"Source: {entry.get('source', 'Cyberpent Radar')}",
    ]
    if cves:
        lines += ["", "CVEs: " + ", ".join(cves[:5])]
    lines += [
        "",
        f"Full briefing: {entry['url']}",
        "",
        "#cybersecurity #threatintel #infosec "
        + "#" + re.sub(r"[^a-z]", "", entry["severity"].lower()),
    ]
    text = "\n".join(lines)
    return text[:MAX_LEN]


def share(entry: dict) -> bool:
    """entry keys: title, severity, confidence, source, cves, stem."""
    if not configured():
        log.info("LinkedIn sharing disabled - missing LINKEDIN_* secrets.")
        return False

    posted = _load_posted()
    entry_url = permalink_for(entry["stem"])
    key = entry_url
    if key in posted:
        log.info("Already shared to LinkedIn: %s", key)
        return False

    payload = {
        "author": _env("LINKEDIN_ORG_URN"),
        "commentary": compose({**entry, "url": entry_url}),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": "",
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    try:
        token = _access_token()
        resp = requests.post(
            f"{API_BASE}/rest/posts",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "LinkedIn-Version": LINKEDIN_VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            log.error("LinkedIn post failed (%s): %s", resp.status_code,
                      resp.text[:300])
            return False
        urn = resp.headers.get("x-restli-id", "")
        log.info("Shared to LinkedIn %s (%s)", entry["title"][:40],
                 f"https://www.linkedin.com/feed/update/{urn}" if urn else "ok")
        posted.add(key)
        _save_posted(posted)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("LinkedIn sharing error: %s", exc)
        return False


def share_all(entries: list) -> int:
    count = 0
    for entry in entries:
        if share(entry):
            count += 1
    return count
