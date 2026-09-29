"""Multi-Account Health, Cooldown, and Telemetry Registry.

Tracks per-account session states, IP bindings, comments posted,
and cooldown windows to prevent anti-bot detection and account burnout.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REGISTRY_FILE = ROOT / "account_registry.json"


def _read_registry() -> dict[str, Any]:
    """Read the current account registry from disk."""
    if not REGISTRY_FILE.exists():
        return {}
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_registry(data: dict[str, Any]) -> None:
    """Save the updated account registry to disk."""
    try:
        REGISTRY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logging.warning("[AccountTracker] Failed to write registry: %s", exc)


def get_account_record(username: str) -> dict[str, Any]:
    """Get the current profile and usage data for an account."""
    clean = username.strip().lower()
    reg = _read_registry()
    now_iso = datetime.now(timezone.utc).isoformat()
    return reg.get(clean, {
        "username": clean,
        "status": "ready",           # ready | active | cooling | challenge | rate_limited | failed
        "last_active": None,
        "last_ip": "UNKNOWN",
        "last_location": "UNKNOWN",
        "total_comments_posted": 0,
        "session_count": 0,
        "last_error": None,
        "created_at": now_iso,
    })


def update_account_record(username: str, **kwargs) -> dict[str, Any]:
    """Update fields for a specific account record."""
    clean = username.strip().lower()
    reg = _read_registry()
    rec = reg.get(clean, {
        "username": clean,
        "status": "ready",
        "last_active": None,
        "last_ip": "UNKNOWN",
        "last_location": "UNKNOWN",
        "total_comments_posted": 0,
        "session_count": 0,
        "last_error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    rec.update(kwargs)
    reg[clean] = rec
    _write_registry(reg)
    return rec


def record_session_start(username: str, ip: str, location: str = "") -> None:
    """Mark an account as actively engaged in an automation session."""
    clean = username.strip().lower()
    rec = get_account_record(clean)
    rec["status"] = "active"
    rec["last_ip"] = ip or "UNKNOWN"
    rec["last_location"] = location or "UNKNOWN"
    rec["session_count"] = rec.get("session_count", 0) + 1
    rec["last_active"] = datetime.now(timezone.utc).isoformat()
    rec["last_error"] = None
    update_account_record(clean, **rec)


def record_session_end(username: str, comments_posted: int, status: str = "cooling", error: str | None = None) -> None:
    """Record session outcome, increment posted counter, and enter cooldown."""
    clean = username.strip().lower()
    rec = get_account_record(clean)
    rec["status"] = status
    rec["total_comments_posted"] = rec.get("total_comments_posted", 0) + max(0, comments_posted)
    rec["last_active"] = datetime.now(timezone.utc).isoformat()
    if error:
        rec["last_error"] = error
    update_account_record(clean, **rec)


def is_account_cooling(username: str, cooldown_minutes: int = 15) -> tuple[bool, int]:
    """Check if an account is within its mandatory cooldown period.
    
    Returns (is_cooling, remaining_seconds).
    """
    rec = get_account_record(username)
    last_str = rec.get("last_active")
    if not last_str or cooldown_minutes <= 0:
        return False, 0

    try:
        last_dt = datetime.fromisoformat(last_str)
        now_dt = datetime.now(timezone.utc)
        elapsed = (now_dt - last_dt).total_seconds()
        needed = cooldown_minutes * 60
        if elapsed < needed:
            remaining = int(needed - elapsed)
            return True, remaining
    except Exception:
        pass
    return False, 0


def get_all_account_telemetry() -> list[dict[str, Any]]:
    """Retrieve telemetry for all tracked accounts."""
    reg = _read_registry()
    return list(reg.values())
