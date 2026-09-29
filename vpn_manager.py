"""VPN and IP Rotation Manager for Instagram PR Bot.

High-performance, dynamic IP verification and automated VPN switching.
Supports CLI automation (Windscribe, ProtonVPN, Cloudflare WARP, OpenVPN, WireGuard),
custom batch scripts, and dynamic IP polling without blind waits.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "monitor.log"
DEFAULT_BATCH_SCRIPT = ROOT / "rotate_vpn.bat"
DEFAULT_PS1_SCRIPT = ROOT / "rotate_vpn.ps1"


def get_ip_details(timeout: float = 3.5) -> dict[str, str]:
    """Retrieve public IP address and rich geolocation details (country, city, ISP)."""
    # Endpoint 1: ip-api.com (rich JSON with country, city, ISP)
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                ip = data.get("query", "").strip()
                country = data.get("country", "")
                code = data.get("countryCode", "")
                city = data.get("city", "")
                isp = data.get("isp", "")
                summary = f"{country} ({city}) • {isp}" if city else f"{country} • {isp}"
                return {
                    "ip": ip,
                    "country": country,
                    "country_code": code,
                    "city": city,
                    "isp": isp,
                    "summary": summary,
                }
    except Exception:
        pass

    # Endpoint 2: ipify.org fallback
    try:
        req = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "curl/7.68.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ip = data.get("ip", "").strip()
            if ip:
                return {
                    "ip": ip,
                    "country": "Unknown",
                    "country_code": "",
                    "city": "",
                    "isp": "",
                    "summary": f"{ip}",
                }
    except Exception:
        pass

    # Endpoint 3: icanhazip.com fallback
    try:
        req = urllib.request.Request("https://icanhazip.com", headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ip = resp.read().decode("utf-8").strip()
            if ip:
                return {
                    "ip": ip,
                    "country": "Unknown",
                    "country_code": "",
                    "city": "",
                    "isp": "",
                    "summary": f"{ip}",
                }
    except Exception:
        pass

    return {
        "ip": "UNKNOWN",
        "country": "Unknown",
        "country_code": "",
        "city": "",
        "isp": "",
        "summary": "Offline / Unreachable",
    }


def get_current_ip(timeout: float = 3.5) -> str:
    """Convenience helper returning just the current IP string."""
    return get_ip_details(timeout=timeout).get("ip", "UNKNOWN")


def detect_installed_vpn() -> dict[str, str]:
    """Scan standard Windows installation paths for supported VPN CLIs."""
    tools: dict[str, str] = {}

    # 1. Custom scripts in project root
    if DEFAULT_BATCH_SCRIPT.exists():
        tools["custom_bat"] = str(DEFAULT_BATCH_SCRIPT)
    if DEFAULT_PS1_SCRIPT.exists():
        tools["custom_ps1"] = str(DEFAULT_PS1_SCRIPT)

    # 2. Windscribe CLI
    windscribe_candidates = [
        shutil.which("windscribe-cli"),
        shutil.which("windscribe"),
        r"C:\Program Files\Windscribe\windscribe-cli.exe",
        r"C:\Program Files (x86)\Windscribe\windscribe-cli.exe",
    ]
    for c in windscribe_candidates:
        if c and Path(c).exists():
            tools["windscribe"] = str(c)
            break

    # 3. Proton VPN CLI
    proton_candidates = [
        shutil.which("protonvpn-cli"),
        shutil.which("protonvpn"),
        r"C:\Program Files\Proton\VPN\ProtonVPN.exe",
        r"C:\Program Files (x86)\Proton\VPN\ProtonVPN.exe",
    ]
    for c in proton_candidates:
        if c and Path(c).exists():
            tools["proton"] = str(c)
            break

    # 4. Cloudflare WARP CLI
    warp_candidates = [
        shutil.which("warp-cli"),
        r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe",
    ]
    for c in warp_candidates:
        if c and Path(c).exists():
            tools["warp"] = str(c)
            break

    # 5. OpenVPN CLI
    openvpn_candidates = [
        shutil.which("openvpn"),
        r"C:\Program Files\OpenVPN\bin\openvpn.exe",
        r"C:\Program Files (x86)\OpenVPN\bin\openvpn.exe",
    ]
    for c in openvpn_candidates:
        if c and Path(c).exists():
            tools["openvpn"] = str(c)
            break

    # 6. WireGuard
    wireguard_candidates = [
        shutil.which("wireguard"),
        r"C:\Program Files\WireGuard\wireguard.exe",
    ]
    for c in wireguard_candidates:
        if c and Path(c).exists():
            tools["wireguard"] = str(c)
            break

    return tools


def rotate_vpn(
    command: str | None = None,
    provider: str = "auto",
    cooldown_seconds: int = 8,
) -> dict[str, Any]:
    """Execute VPN rotation with dynamic polling and rich location verification."""
    old_info = get_ip_details(timeout=3.0)
    old_ip = old_info.get("ip", "UNKNOWN")
    logging.info("[VPN] Current public IP before rotation: %s (%s)", old_ip, old_info.get("summary", ""))

    cmd_to_run = ""
    installed = detect_installed_vpn()

    if command and command.strip():
        cmd_to_run = command.strip()
    elif provider == "auto":
        if "custom_bat" in installed:
            cmd_to_run = f'cmd.exe /c "{installed["custom_bat"]}"'
        elif "custom_ps1" in installed:
            cmd_to_run = f'powershell -ExecutionPolicy Bypass -File "{installed["custom_ps1"]}"'
        elif "windscribe" in installed:
            cmd_to_run = f'"{installed["windscribe"]}" connect best'
        elif "proton" in installed:
            cmd_to_run = f'"{installed["proton"]}" c -f'
        elif "warp" in installed:
            cmd_to_run = f'"{installed["warp"]}" disconnect && "{installed["warp"]}" connect'
        else:
            cmd_to_run = ""
    elif provider in installed:
        if provider == "windscribe":
            cmd_to_run = f'"{installed["windscribe"]}" connect best'
        elif provider == "proton":
            cmd_to_run = f'"{installed["proton"]}" c -f'
        elif provider == "warp":
            cmd_to_run = f'"{installed["warp"]}" disconnect && "{installed["warp"]}" connect'
        else:
            cmd_to_run = installed[provider]

    if not cmd_to_run:
        msg = (
            "No VPN CLI or custom script configured. Add rotation commands to rotate_vpn.bat "
            "or install a free CLI (Windscribe, ProtonVPN, Cloudflare WARP)."
        )
        logging.warning("[VPN] %s", msg)
        return {
            "success": False,
            "old_ip": old_ip,
            "new_ip": old_ip,
            "changed": False,
            "location": old_info.get("summary", ""),
            "message": msg,
        }

    logging.info("[VPN] Executing rotation command: %s", cmd_to_run)
    try:
        proc = subprocess.run(
            cmd_to_run,
            shell=True,
            capture_output=True,
            text=True,
            timeout=35,
        )
        if proc.returncode != 0 and proc.stderr:
            logging.warning("[VPN] Command exit code %d: %s", proc.returncode, proc.stderr.strip()[:180])
    except Exception as exc:
        logging.error("[VPN] Failed to execute rotation command: %s", exc)
        return {
            "success": False,
            "old_ip": old_ip,
            "new_ip": old_ip,
            "changed": False,
            "location": old_info.get("summary", ""),
            "message": f"Execution error: {exc}",
        }

    # Flush DNS cache on Windows
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=5)
    except Exception:
        pass

    # Dynamic Polling: Check if IP changes without waiting out a rigid sleep duration
    start_time = time.time()
    max_wait = max(cooldown_seconds * 2, 14)
    poll_interval = 1.8
    new_info = old_info
    changed = False

    logging.info("[VPN] Monitoring network interface for IP change (up to %ds)...", max_wait)
    while time.time() - start_time < max_wait:
        time.sleep(poll_interval)
        candidate = get_ip_details(timeout=2.5)
        candidate_ip = candidate.get("ip", "UNKNOWN")

        if candidate_ip != "UNKNOWN":
            new_info = candidate
            if candidate_ip != old_ip:
                changed = True
                elapsed = time.time() - start_time
                logging.info(
                    "✅ [VPN] Verified IP change in %.1fs: %s -> %s [%s]",
                    elapsed, old_ip, candidate_ip, candidate.get("summary", "")
                )
                break

    new_ip = new_info.get("ip", "UNKNOWN")
    loc = new_info.get("summary", "")

    if changed:
        msg = f"Rotated IP from {old_ip} -> {new_ip} [{loc}]"
    elif new_ip != "UNKNOWN":
        msg = f"Network online: active IP is {new_ip} [{loc}]"
    else:
        msg = "Network connection unsettled; internet may be momentarily offline"

    return {
        "success": (new_ip != "UNKNOWN"),
        "old_ip": old_ip,
        "new_ip": new_ip,
        "changed": changed,
        "country": new_info.get("country", ""),
        "city": new_info.get("city", ""),
        "isp": new_info.get("isp", ""),
        "location": loc,
        "message": msg,
    }


def main():
    """CLI utility entrypoint."""
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

    if action == "status":
        details = get_ip_details()
        tools = detect_installed_vpn()
        print(json.dumps({"ip_details": details, "detected_tools": tools}, indent=2))
    elif action in ("rotate", "switch"):
        cmd = sys.argv[2] if len(sys.argv) > 2 else None
        res = rotate_vpn(command=cmd)
        print(json.dumps(res, indent=2))
    else:
        print(f"Usage: python vpn_manager.py [status|rotate] [optional_command]")


if __name__ == "__main__":
    main()
