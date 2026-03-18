#!/usr/bin/env python3
"""
scripts/google_auth.py — One-time Google OAuth setup for Jibsa.

Run this on your LOCAL machine (the one with a browser).
It opens Google's consent page, you click Allow, and it prints a JSON token
block that you paste into Slack as:  @Jibsa google token <paste here>

Usage:
    python3 scripts/google_auth.py
    # or with explicit credentials:
    GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python3 scripts/google_auth.py
"""
import json
import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def main():
    # Try loading .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id:
        client_id = input("GOOGLE_CLIENT_ID: ").strip()
    if not client_secret:
        client_secret = input("GOOGLE_CLIENT_SECRET: ").strip()

    if not client_id or not client_secret:
        print("ERROR: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed.")
        print("Run: pip install google-auth-oauthlib")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    token_json = json.dumps({
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    })

    print("\n" + "=" * 60)
    print("Authorization successful! Copy the line below and paste")
    print("it into Slack (in the #ai-agent-team channel):")
    print("=" * 60)
    print(f"\n@Jibsa google token {token_json}\n")
    print("=" * 60)


if __name__ == "__main__":
    main()
