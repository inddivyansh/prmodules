@echo off
REM ===========================================================================
REM Custom VPN Rotation Script for Indian Army PR Bot
REM This script is automatically called by the bot before switching Instagram accounts.
REM You can uncomment and adapt one of the free options below or use your own VPN CLI.
REM ===========================================================================

REM --- OPTION 1: Windscribe CLI (Free 10GB/mo) ---
REM "C:\Program Files\Windscribe\windscribe-cli.exe" connect best

REM --- OPTION 2: ProtonVPN CLI (Free Unlimited) ---
REM protonvpn-cli c -f

REM --- OPTION 3: Cloudflare WARP (Free) ---
REM "C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe" disconnect
REM timeout /t 2 /nobreak >nul
REM "C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe" connect

REM --- OPTION 4: WireGuard or OpenVPN Profile Switch ---
REM openvpn.exe --config "C:\path\to\free_server.ovpn"

echo [VPN Script] Hook triggered for account rotation.
