"""Single-Reel Comment Bot.

Given a single Instagram reel/post URL, this script logs in with EVERY
configured account and posts a (differently-generated) counter-comment on
that one specific post.

Usage (standalone):
    python single_reel_bot.py --url https://www.instagram.com/reel/XXXXX/

Usage (spawned by app.py):
    Written to bot_config.json as  {"single_reel_url": "https://..."}
    then: python single_reel_bot.py
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone

import ollama

from shared import (
    ACCOUNTS_FILE,
    ROOT,
    NegativePost,
    ResponseRecord,
    _is_logged_in,
    _save_cookies,
    append_negative_post,
    append_response_log,
    configure_logging,
    create_driver,
    extract_media_id,
    extract_post_data,
    get_cookies_file,
    is_already_commented_by_user,
    load_accounts,
    load_config,
    login_instagram,
    mark_post_seen,
    post_comment,
)
from account_tracker import (
    is_account_cooling,
    record_session_end,
    record_session_start,
)
from vpn_manager import get_ip_details, rotate_vpn
from bot import (
    ensure_ollama_server,
    generate_response,
    is_negative_post,
)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_bot_config() -> dict:
    """Load dashboard-written bot_config.json; fall back to safe defaults."""
    import json as _json
    cfg_path = ROOT / "bot_config.json"
    defaults = {
        "model": "llama3.2",
        "delay_seconds": 120,
        "max_comment_length": 220,
        "headless": False,
        "comment_prefix": "",
        "enable_vpn_rotation": False,
        "vpn_rotate_command": "",
        "vpn_cooldown_seconds": 8,
        "account_cooldown_minutes": 15,
        "single_reel_url": "",
        "single_reel_skip_negative_check": False,
    }
    if cfg_path.exists():
        try:
            data = _json.loads(cfg_path.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception as exc:
            logging.warning("Could not read bot_config.json: %s -- using defaults", exc)
    return defaults


# ---------------------------------------------------------------------------
# Core single-reel loop
# ---------------------------------------------------------------------------

def comment_on_single_reel(
    reel_url: str,
    model: str,
    delay_seconds: int,
    max_len: int,
    comment_prefix: str,
    headless: bool,
    enable_vpn: bool,
    vpn_cmd: str,
    vpn_cooldown: int,
    account_cooldown_min: int,
    skip_negative_check: bool,
    accounts: list,
) -> int:
    """Iterate over all accounts and post a generated comment on *reel_url*.

    Returns the total number of successfully posted comments.
    """
    media_id = extract_media_id(reel_url)
    if not media_id:
        logging.error("Could not extract a media ID from URL: %s", reel_url)
        return 0

    logging.info(
        "Single-Reel Mode -- target: %s  (media_id: %s)  accounts: %d",
        reel_url, media_id, len(accounts),
    )

    total_posted = 0
    # Track every comment posted on this reel so the next account gets something unique
    used_comments: list[str] = []

    for idx, (acct_user, acct_pwd) in enumerate(accounts):
        # Safety cooldown check
        is_cooling, rem_sec = is_account_cooling(acct_user, cooldown_minutes=account_cooldown_min)
        if is_cooling:
            logging.info(
                "[%d/%d] @%s is in safety cooldown (%ds remaining) -- skipping",
                idx + 1, len(accounts), acct_user, rem_sec,
            )
            continue

        logging.info(
            "=================================================\n"
            "  SINGLE-REEL ACCOUNT [%d/%d]: @%s\n"
            "=================================================",
            idx + 1, len(accounts), acct_user,
        )

        # VPN rotation (optional)
        current_ip = "UNKNOWN"
        current_loc = ""
        if enable_vpn:
            vpn_res = rotate_vpn(command=vpn_cmd, cooldown_seconds=vpn_cooldown)
            current_ip = str(vpn_res.get("new_ip", "UNKNOWN"))
            current_loc = str(vpn_res.get("location", ""))
            logging.info("VPN: %s", vpn_res.get("message", "rotation finished"))
        else:
            net_info = get_ip_details(timeout=2.5)
            current_ip = str(net_info.get("ip", "UNKNOWN"))
            current_loc = str(net_info.get("summary", ""))

        record_session_start(acct_user, ip=current_ip, location=current_loc)

        p_cookie, a_cookie = get_cookies_file(acct_user)
        cookies_exist = p_cookie.exists() or a_cookie.exists()
        is_headless = headless and cookies_exist

        driver = None
        posted = 0
        try:
            driver = create_driver(is_headless, username=acct_user)
            if not login_instagram(driver, acct_user, acct_pwd):
                logging.warning("Skipping @%s -- login/checkpoint challenge", acct_user)
                record_session_end(acct_user, comments_posted=0, status="challenge_required", error="Authentication challenge")
                continue

            try:
                _save_cookies(driver, username=acct_user)
            except Exception:
                pass

            # Check if this account already commented on this post
            driver.get(reel_url)
            time.sleep(3)
            if is_already_commented_by_user(driver, acct_user):
                logging.info("  [skip] @%s already commented on this reel", acct_user)
                record_session_end(acct_user, comments_posted=0, status="already_commented")
                continue

            # Extract post data
            post = extract_post_data(driver, reel_url)
            if not post:
                logging.warning("  [skip] could not extract post data for %s", reel_url)
                record_session_end(acct_user, comments_posted=0, status="failed", error="Post extraction failed")
                continue

            caption = post.get("caption", "")

            # Optional sentiment check (for logging purposes only in single-reel mode)
            if not skip_negative_check:
                neg = is_negative_post(caption, model)
                if not neg:
                    logging.info("  [info] Post not classified as negative -- commenting anyway (single-reel mode)")

            # Generate a unique response per account (exclude all prior comments on this reel)
            response_text = generate_response(
                caption or "[No caption]",
                model,
                max_len,
                exclude_texts=used_comments,
            )
            if not response_text:
                logging.warning("  [skip] response generation failed for @%s", acct_user)
                record_session_end(acct_user, comments_posted=0, status="failed", error="Response generation failed")
                continue

            # Apply prefix
            if comment_prefix:
                response_text = f"{comment_prefix} {response_text}"
                if len(response_text) > max_len:
                    response_text = response_text[:max_len].rsplit(" ", 1)[0].rstrip(".,;: ") + "."

            # Post the comment
            success = post_comment(driver, reel_url, response_text)
            status = "posted" if success else "failed"

            # Persist records
            append_negative_post(NegativePost(
                media_id=media_id,
                username=post.get("username", ""),
                permalink=reel_url,
                caption=caption.replace("\n", " ")[:500],
                source_tag="single_reel",
                sentiment="targeted",
                collected_at=datetime.now(timezone.utc).isoformat(),
                response_status=status,
            ))
            append_response_log(ResponseRecord(
                media_id=media_id,
                permalink=reel_url,
                caption_snippet=caption[:120],
                generated_response=response_text,
                status=status,
                responded_at=datetime.now(timezone.utc).isoformat(),
            ))
            mark_post_seen(f"{media_id}_{acct_user}", status, reel_url)

            if success:
                posted += 1
                total_posted += 1
                # Register comment so the next account generates something distinct
                used_comments.append(response_text)
                logging.info(
                    "  [posted] @%s successfully commented on reel (total posted: %d/%d accounts)",
                    acct_user, total_posted, len(accounts),
                )
                try:
                    _save_cookies(driver, username=acct_user)
                except Exception:
                    pass
            else:
                logging.warning("  [failed] @%s could not post comment", acct_user)

            record_session_end(acct_user, comments_posted=posted, status="cooling" if posted > 0 else "ready")

        except Exception as exc:
            logging.error("Session error on @%s: %s", acct_user, exc)
            record_session_end(acct_user, comments_posted=posted, status="failed", error=str(exc))
        finally:
            if driver:
                try:
                    if _is_logged_in(driver):
                        _save_cookies(driver, username=acct_user)
                except Exception:
                    pass
                driver.quit()

        # Inter-account delay
        if idx < len(accounts) - 1:
            delay = random.uniform(delay_seconds * 0.8, delay_seconds * 1.3)
            logging.info("  Inter-account pause: %.0fs before next account...", delay)
            time.sleep(delay)

    logging.info(
        "Single-Reel session complete -- %d comment(s) posted across %d account(s)",
        total_posted, len(accounts),
    )
    return total_posted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    load_config()
    configure_logging()

    parser = argparse.ArgumentParser(description="Single-Reel Comment Bot")
    parser.add_argument("--url", default="", help="Instagram reel/post URL to target")
    args = parser.parse_args()

    cfg = _load_bot_config()

    reel_url = args.url.strip() or cfg.get("single_reel_url", "").strip()
    if not reel_url:
        logging.error("No reel URL provided. Pass --url or set single_reel_url in bot_config.json")
        return 2

    # Normalise URL
    if not reel_url.startswith("http"):
        reel_url = "https://www.instagram.com/reel/" + reel_url.strip("/") + "/"

    model = cfg["model"]
    delay_seconds = int(cfg["delay_seconds"])
    max_len = int(cfg["max_comment_length"])
    headless = bool(cfg["headless"])
    comment_prefix = cfg["comment_prefix"]
    enable_vpn = bool(cfg.get("enable_vpn_rotation", False))
    vpn_cmd = str(cfg.get("vpn_rotate_command", "")).strip()
    vpn_cooldown = int(cfg.get("vpn_cooldown_seconds", 8))
    account_cooldown_min = int(cfg.get("account_cooldown_minutes", 15))
    skip_negative_check = bool(cfg.get("single_reel_skip_negative_check", False))

    # Load credentials
    username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    password = os.getenv("INSTAGRAM_PASSWORD", "").strip()
    accounts = load_accounts()
    if not accounts and username and password:
        accounts = [(username, password)]

    if not accounts:
        logging.error("No Instagram credentials found. Configure INSTAGRAM_USERNAME/PASSWORD or accounts.txt")
        return 2

    ensure_ollama_server()

    comment_on_single_reel(
        reel_url=reel_url,
        model=model,
        delay_seconds=delay_seconds,
        max_len=max_len,
        comment_prefix=comment_prefix,
        headless=headless,
        enable_vpn=enable_vpn,
        vpn_cmd=vpn_cmd,
        vpn_cooldown=vpn_cooldown,
        account_cooldown_min=account_cooldown_min,
        skip_negative_check=skip_negative_check,
        accounts=accounts,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
