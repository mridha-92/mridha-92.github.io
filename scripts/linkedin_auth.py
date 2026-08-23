#!/usr/bin/env python3
"""One-time LinkedIn authorization helper (runs locally, NOT in Actions).

Flow:
  1. Starts a temporary localhost HTTP listener for the OAuth redirect.
  2. Opens your browser -> you log in as a Company Page super admin.
  3. Exchanges the auth code for access + refresh tokens.
  4. Lists the Company Pages this account administers (org URNs).
  5. Prints the values to store as GitHub Actions secrets.

Usage:
    set LINKEDIN_CLIENT_ID=...     (from your LinkedIn app)
    set LINKEDIN_CLIENT_SECRET=...
    python scripts/linkedin_auth.py
"""
import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser

import requests

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:5319/callback"
SCOPES = "r_organization_social w_organization_social"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
PORT = 5319


class Callback(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        Callback.code = urllib.parse.parse_qs(query).get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorization received - you can close this tab.</h2>")

    def log_message(self, *args):  # silence default noise
        pass


def main() -> int:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET env vars first.")
        return 1

    server = http.server.HTTPServer(("localhost", PORT), Callback)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "cp-radar-auth",
    })
    webbrowser.open(f"{AUTH_URL}?{params}")
    print("Waiting for authorization in your browser...")

    for _ in range(300):  # up to 5 minutes
        if Callback.code:
            break
        threading.Event().wait(1)
    else:
        print("Timed out waiting for authorization.")
        return 1

    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": Callback.code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=30)
    resp.raise_for_status()
    tokens = resp.json()

    print("\n=== ADD THESE AS GITHUB ACTIONS SECRETS ===")
    print(f"LINKEDIN_REFRESH_TOKEN={tokens.get('refresh_token', '(none returned!)')}")
    print(f"(access token for reference, expires_in={tokens.get('expires_in')}s)")

    access = tokens.get("access_token", "")
    if access:
        acl = requests.get(
            "https://api.linkedin.com/v2/organizationalEntityAcls",
            params={
                "q": "roleAssignee",
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
                "projection": "(elements*(organizationalTarget~(localizedName,vanityName)))",
            },
            headers={"Authorization": f"Bearer {access}"},
            timeout=30,
        )
        print("\n=== PAGES YOU ADMINISTER ===")
        try:
            for el in acl.json().get("elements", []):
                target = el.get("organizationalTarget~", {})
                urn = el.get("organizationalTarget", "")
                print(f"{target.get('localizedName', '?')}: {urn}")
        except Exception as exc:  # noqa: BLE001
            print(f"Could not list pages ({exc}). Raw: {acl.text[:400]}")
        print("\nSet LINKEDIN_ORG_URN to the urn:li:organization:NNN value above.")
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
