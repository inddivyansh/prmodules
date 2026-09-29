"""VPN and IP Rotation Manager for Instagram PR Bot.

Supports automated VPN switching via CLI tools (Windscribe, ProtonVPN, Cloudflare WARP,
OpenVPN, WireGuard), custom CLI commands, or custom batch/powershell scripts.
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

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "monitor.log"
DEFAULT_BATCH_SCRIPT = ROOT / "rotate_vpn.bat"
DEFAULT_PS1_SCRIPT = ROOT / "rotate_vpn.ps1"


def get_current_ip(timeout: float = 4.0) -> str:
    """Retrieve public IP address from reliable external endpoints."""
    endpoints = (
        "https://api.ipify.org?format=json",
        "https://api64.ipify.org?format=json",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
    )
    for url in endpoints:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "curl/7.68.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8").strip()
                if raw.startswith("{"):
                    data = json.loads(raw)
                    return data.get("ip", "").strip()
                return raw.strip()
        except Exception:
            continue
    return "UNKNOWN"


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
) -> dict[str, str | bool]:
    """Execute VPN rotation and verify public IP change."""
    old_ip = get_current_ip()
    logging.info("[VPN] Current public IP before rotation: %s", old_ip)

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
            "No VPN CLI or custom script found. To automate VPN rotation, install a free CLI "
            "(Windscribe, ProtonVPN, or Cloudflare WARP) or place commands in rotate_vpn.bat."
        )
        logging.warning("[VPN] %s", msg)
        return {
            "success": False,
            "old_ip": old_ip,
            "new_ip": old_ip,
            "changed": False,
            "message": msg,
        }

    logging.info("[VPN] Executing rotation command: %s", cmd_to_run)
    try:
        proc = subprocess.run(
            cmd_to_run,
            shell=True,
            capture_output=True,
            text=True,
            timeout=40,
        )
        if proc.returncode != 0 and proc.stderr:
            logging.warning("[VPN] Command returned code %d: %s", proc.returncode, proc.stderr.strip()[:200])
    except Exception as exc:
        logging.error("[VPN] Failed to execute rotation command: %s", exc)
        return {
            "success": False,
            "old_ip": old_ip,
            "new_ip": old_ip,
            "changed": False,
            "message": f"Execution error: {exc}",
        }

    # Allow network interfaces and routing table to settle
    logging.info("[VPN] Waiting %ds for network routes and DNS to settle...", cooldown_seconds)
    time.sleep(cooldown_seconds)

    # Optional DNS cache flush on Windows
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=5)
    except Exception:
        pass

    new_ip = get_current_ip()
    changed = (new_ip != old_ip) and (new_ip != "UNKNOWN")
    logging.info("[VPN] IP check post-rotation: %s (changed=%s)", new_ip, changed)

    return {
        "success": True,
        "old_ip": old_ip,
        "new_ip": new_ip,
        "changed": changed,
        "message": f"Switched from {old_ip} to {new_ip}" if changed else f"Active IP is {new_ip}",
    }


def main():
    """CLI utility entrypoint."""
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

    if action == "status":
        ip = get_current_ip()
        tools = detect_installed_vpn()
        print(json.dumps({"ip": ip, "detected_tools": tools}, indent=2))
    elif action in ("rotate", "switch"):
        cmd = sys.argv[2] if len(sys.argv) > 2 else None
        res = rotate_vpn(command=cmd)
        print(json.dumps(res, indent=2))
    else:
        print(f"Usage: python vpn_manager.py [status|rotate] [optional_command]")


if __name__ == "__main__":
    main()
