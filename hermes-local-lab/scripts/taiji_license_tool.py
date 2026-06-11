#!/usr/bin/env python3
"""Internal helper for issuing offline Taiji Agent license JWTs."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt


PRODUCT = "taiji-agent"
PRIVATE_KEY_ENV = "TAIJI_LICENSE_PRIVATE_KEY_FILE"


def _parse_date(value: str | None, *, default: datetime) -> datetime:
    if not value:
        return default
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _features(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue a signed Taiji Agent trial license.")
    parser.add_argument("--customer", required=True, help="Customer display name.")
    parser.add_argument("--days", type=int, required=True, help="Validity duration in days.")
    parser.add_argument("--output", default="license.jwt", help="Output token path.")
    parser.add_argument("--license-id", default="", help="Stable license id. Defaults to lic-<timestamp>.")
    parser.add_argument("--not-before", default="", help="ISO start time. Defaults to now.")
    parser.add_argument("--features", default="chat,writing", help="Comma-separated feature list.")
    parser.add_argument("--max-version", default="", help="Optional maximum supported app version.")
    args = parser.parse_args(argv)

    if args.days <= 0:
        raise SystemExit("--days must be greater than 0")

    private_key_file = os.environ.get(PRIVATE_KEY_ENV, "").strip()
    if not private_key_file:
        raise SystemExit(f"Set {PRIVATE_KEY_ENV} to the signing private key path.")
    private_key = Path(private_key_file).expanduser().read_text(encoding="utf-8")

    now = datetime.now(timezone.utc)
    nbf = _parse_date(args.not_before, default=now)
    exp = nbf + timedelta(days=args.days)
    license_id = args.license_id.strip() or f"lic-{int(time.time())}"
    payload = {
        "license_id": license_id,
        "customer": args.customer,
        "product": PRODUCT,
        "aud": PRODUCT,
        "iat": int(now.timestamp()),
        "issued_at": _iso(now),
        "nbf": int(nbf.timestamp()),
        "not_before": _iso(nbf),
        "exp": int(exp.timestamp()),
        "expires_at": _iso(exp),
        "features": _features(args.features),
    }
    if args.max_version.strip():
        payload["max_version"] = args.max_version.strip()

    token = jwt.encode(payload, private_key, algorithm="RS256")
    output = Path(args.output).expanduser()
    output.write_text(token + "\n", encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
