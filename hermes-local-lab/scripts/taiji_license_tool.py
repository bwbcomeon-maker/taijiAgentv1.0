#!/usr/bin/env python3
"""Internal helper for issuing offline Taiji Agent license JWTs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt


PRODUCT = "taiji-agent"
PRIVATE_KEY_ENV = "TAIJI_LICENSE_PRIVATE_KEY_FILE"
MACHINE_BINDING_TYPE = "machine_fingerprint_v3"
MACHINE_CODE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTIVATION_MODE_OFFLINE_MACHINE_FILE = "offline_machine_file"
BLOCKING_RISK_FLAGS = {"no_device_secret", "device_secret_unavailable", "no_stable_hardware"}


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


def _machine_code_short(machine_code: str) -> str:
    text = str(machine_code or "").strip()
    return text.split(":", 1)[1][:12] if text.startswith("sha256:") else text[:12]


def _safe_filename_part(value: str, fallback: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", str(value or "").strip())
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-.")
    return (text or fallback)[:72]


def _filename_timestamp(dt: datetime) -> str:
    return _iso(dt).replace("-", "").replace(":", "").replace("T", "-")


def _suggest_license_filename(
    *,
    customer: str,
    machine_label: str,
    machine_code: str,
    not_before: datetime,
    expires_at: datetime,
) -> str:
    return "-".join(
        [
            "taiji-license",
            _safe_filename_part(customer, "customer"),
            _safe_filename_part(machine_label, "terminal"),
            _safe_filename_part(_machine_code_short(machine_code), "machine"),
            _filename_timestamp(not_before),
            _filename_timestamp(expires_at),
        ]
    ) + ".jwt"


def _descriptive_output_path(raw: str, suggested_filename: str) -> Path:
    output = Path(raw).expanduser() if raw.strip() else Path(suggested_filename)
    if output.name.lower() in {"license.jwt", "taiji-license.jwt"}:
        return output.with_name(suggested_filename)
    return output


def _machine_binding_from_args(args: argparse.Namespace) -> tuple[str, str, dict]:
    machine_code = args.machine_code.strip().lower()
    machine_label = args.machine_label.strip()
    request: dict = {}
    if args.machine_request:
        request_path = Path(args.machine_request).expanduser()
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Invalid machine request file: {request_path}") from exc
        if not isinstance(request, dict):
            raise SystemExit("Invalid machine request file: expected JSON object")
        if request.get("product") not in (None, PRODUCT):
            raise SystemExit("Machine request product does not match taiji-agent")
        if request.get("binding_type") != MACHINE_BINDING_TYPE:
            raise SystemExit("Machine request must be re-exported with the v3 device-bound machine code")
        machine_code = str(request.get("machine_code") or "").strip().lower()
        machine_label = machine_label or str(request.get("machine_label") or request.get("hostname") or "").strip()
    if not MACHINE_CODE_RE.fullmatch(machine_code):
        raise SystemExit("A valid --machine-request or --machine-code is required")
    device_id = str(request.get("device_id") or "").strip().lower()
    if not MACHINE_CODE_RE.fullmatch(device_id):
        raise SystemExit("Machine request must include a v3 device identity")
    risk_flags = (
        [str(item).strip() for item in request.get("risk_flags", []) if str(item).strip()]
        if isinstance(request.get("risk_flags"), list)
        else []
    )
    blocked = sorted(set(risk_flags) & BLOCKING_RISK_FLAGS)
    if blocked:
        raise SystemExit(f"Machine request quality is insufficient for offline licensing: {', '.join(blocked)}")
    return machine_code, machine_label, request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue a signed Taiji Agent trial license.")
    parser.add_argument("--customer", required=True, help="Customer display name.")
    parser.add_argument("--days", type=int, required=True, help="Validity duration in days.")
    parser.add_argument("--output", default="", help="Output token path. Generic license.jwt is replaced with a descriptive name.")
    parser.add_argument("--license-id", default="", help="Stable license id. Defaults to lic-<timestamp>.")
    parser.add_argument("--not-before", default="", help="ISO start time. Defaults to now.")
    parser.add_argument("--features", default="chat,writing", help="Comma-separated feature list.")
    parser.add_argument("--max-version", default="", help="Optional maximum supported app version.")
    parser.add_argument("--machine-request", default="", help="taiji-machine-request.json exported from the target terminal.")
    parser.add_argument("--machine-code", default="", help="Deprecated. Use --machine-request so v3 device identity is included.")
    parser.add_argument("--machine-label", default="", help="Optional terminal label stored in the license.")
    args = parser.parse_args(argv)

    if args.days <= 0:
        raise SystemExit("--days must be greater than 0")
    machine_code, machine_label, machine_request = _machine_binding_from_args(args)

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
        "activation_mode": ACTIVATION_MODE_OFFLINE_MACHINE_FILE,
        "binding_type": MACHINE_BINDING_TYPE,
        "machine_code": machine_code,
        "machine_code_short": _machine_code_short(machine_code),
        "device_id": str(machine_request.get("device_id") or "").strip().lower(),
        "device_id_short": str(machine_request.get("device_id_short") or "").strip()
        or _machine_code_short(str(machine_request.get("device_id") or "")),
        "machine_request_id": str(machine_request.get("request_id") or "").strip(),
        "machine_request_generated_at": str(machine_request.get("generated_at") or "").strip(),
        "fingerprint_quality": str(machine_request.get("fingerprint_quality") or "unknown").strip(),
        "risk_flags": [str(item).strip() for item in machine_request.get("risk_flags", []) if str(item).strip()]
        if isinstance(machine_request.get("risk_flags"), list)
        else [],
    }
    if machine_label:
        payload["machine_label"] = machine_label
    if args.max_version.strip():
        payload["max_version"] = args.max_version.strip()

    token = jwt.encode(payload, private_key, algorithm="RS256")
    output = _descriptive_output_path(
        args.output,
        _suggest_license_filename(
            customer=args.customer,
            machine_label=machine_label,
            machine_code=machine_code,
            not_before=nbf,
            expires_at=exp,
        ),
    )
    output.write_text(token + "\n", encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
