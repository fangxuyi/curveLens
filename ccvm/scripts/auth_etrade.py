#!/usr/bin/env python
"""
E*TRADE OAuth 1.0a one-time authorization script.

Two-step flow (works without an interactive terminal):

  Step 1 — get the authorization URL:
      python scripts/auth_etrade.py --sandbox --key KEY --secret SECRET

      Opens browser (or prints URL). Saves request token to ~/.ccvm/etrade_request.json.

  Step 2 — complete auth after clicking Accept in the browser:
      python scripts/auth_etrade.py --sandbox --key KEY --secret SECRET --verifier XXXXXX

      Exchanges request token + verifier for access token.
      Saves access token to ~/.ccvm/etrade_tokens.json.

Tokens expire at midnight ET daily.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import ssl
import time
import urllib.parse
import uuid
import webbrowser
from pathlib import Path

_TOKEN_FILE   = Path.home() / ".ccvm" / "etrade_tokens.json"
_REQUEST_FILE = Path.home() / ".ccvm" / "etrade_request.json"   # temp: request token

_LIVE_HOST    = "api.etrade.com"
_SANDBOX_HOST = "apisb.etrade.com"
_AUTH_URL     = "https://us.etrade.com/e/t/etws/authorize?key={key}&token={token}"


def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _oauth1_header(
    url: str,
    method: str,
    consumer_key: str,
    consumer_secret: str,
    token: str = "",
    token_secret: str = "",
    query_params: dict | None = None,
    callback: str | None = "oob",
    verifier: str | None = None,
) -> str:
    params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    if callback is not None:
        params["oauth_callback"] = callback
    if token:
        params["oauth_token"] = token
    if verifier:
        params["oauth_verifier"] = verifier

    all_params: dict[str, str] = {}
    all_params.update(query_params or {})
    all_params.update(params)

    encoded = sorted((_pct(k), _pct(v)) for k, v in all_params.items())
    param_string = "&".join(f"{k}={v}" for k, v in encoded)
    base_url = url.split("?")[0]
    sig_base = f"{method.upper()}&{_pct(base_url)}&{_pct(param_string)}"
    signing_key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    digest = hmac.new(signing_key.encode(), sig_base.encode(), hashlib.sha1).digest()
    params["oauth_signature"] = base64.b64encode(digest).decode()

    header_parts = [("realm", "")] + sorted((k, v) for k, v in params.items())
    parts = ", ".join(f'{k}="{_pct(v)}"' for k, v in header_parts)
    return f"OAuth {parts}"


def _https_get(host: str, path: str, headers: dict) -> tuple[int, str]:
    """GET via http.client — tolerates E*TRADE's Content-Length off-by-4 bug."""
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, context=ctx, timeout=20)
    try:
        conn.request("GET", path, headers={**headers, "Accept": "application/json"})
        resp = conn.getresponse()
        status = resp.status
        try:
            raw = resp.read()
        except http.client.IncompleteRead as e:
            raw = e.partial
        return status, raw.decode(errors="replace")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="E*TRADE OAuth 1.0a authorization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--key",      help="Consumer key (or ETRADE_CONSUMER_KEY env var)")
    parser.add_argument("--secret",   help="Consumer secret (or ETRADE_CONSUMER_SECRET env var)")
    parser.add_argument("--sandbox",  action="store_true", help="Use sandbox environment")
    parser.add_argument("--verifier", help="Verifier code from browser (Step 2)")
    args = parser.parse_args()

    consumer_key    = args.key    or os.environ.get("ETRADE_CONSUMER_KEY", "")
    consumer_secret = args.secret or os.environ.get("ETRADE_CONSUMER_SECRET", "")

    if not consumer_key or not consumer_secret:
        print("ERROR: provide --key and --secret or set ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET")
        raise SystemExit(1)

    host = _SANDBOX_HOST if args.sandbox else _LIVE_HOST
    env  = "sandbox" if args.sandbox else "live"
    _REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Step 2: exchange verifier for access token ─────────────────────────────
    if args.verifier:
        if not _REQUEST_FILE.exists():
            print(f"ERROR: no saved request token at {_REQUEST_FILE}")
            print("Run without --verifier first to get the authorization URL.")
            raise SystemExit(1)

        saved = json.loads(_REQUEST_FILE.read_text())
        req_token        = saved["oauth_token"]
        req_token_secret = saved["oauth_token_secret"]

        print(f"Environment: {env}  ({host})")
        print("Step 2: Exchanging verifier for access token...")

        path = "/oauth/access_token"
        url  = f"https://{host}{path}"
        auth = _oauth1_header(
            url, "GET", consumer_key, consumer_secret,
            token=req_token, token_secret=req_token_secret,
            verifier=args.verifier, callback=None,
        )
        status, body = _https_get(host, path, {"Authorization": auth})

        if status != 200:
            print(f"ERROR: HTTP {status} — {body[:400]}")
            raise SystemExit(1)

        p = dict(urllib.parse.parse_qsl(body.strip()))
        access_token        = p.get("oauth_token", "")
        access_token_secret = p.get("oauth_token_secret", "")
        if not access_token:
            print(f"ERROR: no access token in response: {body}")
            raise SystemExit(1)

        _TOKEN_FILE.write_text(json.dumps({
            "consumer_key": consumer_key,
            "consumer_secret": consumer_secret,
            "access_token": access_token,
            "access_token_secret": access_token_secret,
            "authorized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "environment": env,
        }, indent=2))
        _REQUEST_FILE.unlink(missing_ok=True)  # clean up temp file

        print(f"  Access token saved → {_TOKEN_FILE}")
        print("\nReady. Run the collector:")
        if args.sandbox:
            print(f"  ETRADE_SANDBOX=1 python scripts/collect_day.py --date $(date +%F) --source etrade_options")
        else:
            print(f"  python scripts/collect_day.py --date $(date +%F) --source etrade_options")
        print("\nTokens expire at midnight ET — rerun this script tomorrow.")
        return

    # ── Step 1: get request token and print authorization URL ──────────────────
    print(f"Environment: {env}  ({host})")
    print("Step 1: Getting request token...")

    path1 = "/oauth/request_token"
    url1  = f"https://{host}{path1}"
    auth1 = _oauth1_header(url1, "GET", consumer_key, consumer_secret, callback="oob")
    s1, b1 = _https_get(host, path1, {"Authorization": auth1})

    if s1 != 200:
        print(f"ERROR: HTTP {s1} — {b1[:400]}")
        raise SystemExit(1)

    p1 = dict(urllib.parse.parse_qsl(b1.strip()))
    req_token        = p1.get("oauth_token", "")
    req_token_secret = p1.get("oauth_token_secret", "")
    if not req_token:
        print(f"ERROR: no oauth_token in response: {b1}")
        raise SystemExit(1)

    # Save request token for Step 2
    _REQUEST_FILE.write_text(json.dumps({
        "oauth_token": req_token,
        "oauth_token_secret": req_token_secret,
        "consumer_key": consumer_key,
        "environment": env,
    }))

    auth_url = _AUTH_URL.format(
        key=consumer_key,
        token=urllib.parse.quote(req_token, safe=""),
    )
    print(f"  OK — request token received (valid 5 minutes)\n")
    print("Step 2: Open this URL in your browser, log in, and click Accept:")
    print(f"\n    {auth_url}\n")

    try:
        webbrowser.open(auth_url)
        print("  (browser opened automatically)")
    except Exception:
        pass

    print("\nAfter you see the verifier code, run:")
    print(f"\n    python scripts/auth_etrade.py {'--sandbox ' if args.sandbox else ''}--key {consumer_key} --secret {consumer_secret} --verifier PASTE_CODE_HERE\n")


if __name__ == "__main__":
    main()
