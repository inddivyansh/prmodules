"""Indian Army PR Bot — All-in-one.

Scans Instagram hashtags/keywords, detects negative posts via Ollama,
generates a counter-response, and posts the comment — all in one pass.
"""
from __future__ import annotations

import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import ollama

from shared import (
    ACCOUNTS_FILE,
    ALT_COOKIES_FILE,
    COOKIES_FILE,
    ROOT,
    NegativePost,
    ResponseRecord,
    _is_logged_in,
    _save_cookies,
    append_negative_post,
    append_response_log,
    bounded_int,
    collect_post_links,
    configure_logging,
    create_driver,
    env_list,
    extract_media_id,
    extract_post_data,
    fetch_trending_topics,
    get_cookies_file,
    is_already_commented_by_user,
    is_post_seen,
    load_accounts,
    load_config,
    login_instagram,
    mark_post_seen,
    parse_post_age_days,
    pause,
    post_comment,
    read_existing_media_ids,
    update_post_status,
)
from vpn_manager import get_current_ip, get_ip_details, rotate_vpn
from account_tracker import (
    get_account_record,
    is_account_cooling,
    record_session_end,
    record_session_start,
)



# ---------------------------------------------------------------------------
# Sentiment detection via Ollama
# ---------------------------------------------------------------------------

SENTIMENT_PROMPT = """You are an extremely strict sentiment classifier for social-media posts about the Indian Army. Your job is to be VERY aggressive in detecting negativity. When in doubt, ALWAYS say YES.

AUTOMATIC YES — if the post contains ANY of these hashtags or terms, reply YES immediately:
#indianarmycrimes, #armyatrocities, #kashmirviolence, #humanrightsviolation, #armyabuse, #militarytorture, #falseencounter, #fakencounter, #antinational, #armycrimes, #indianmilitarycrimes, #armyfail, #armyexposed, #endmilitaryrule, #AFSPA, भारतीयसेनाकेअपराध, सेनाअत्याचार, सेनाकीहिंसा, فوجیمظالم, فوجیظلم, بھارتیفوجکےجرائم

A post is ALSO negative if it does ANY of the following even slightly:
- Criticises, questions, or doubts the Indian Army or Indian Armed Forces in any way
- Accuses, blames, or holds the army responsible for anything negative
- Mocks, ridicules, or makes sarcastic remarks about the army
- Spreads any claim, rumour, or allegation that could damage the army's reputation
- Mentions human rights violations, atrocities, or misconduct by the army
- Questions army operations, decisions, or leadership
- Compares the army unfavourably to other forces
- Uses negative tone, language, or hashtags related to the army
- Even subtly implies the army did something wrong
- Contains anti-national or anti-military sentiment of any kind

Be VERY strict. Even if only 1% of the post seems negative toward the Indian Army, reply YES.
If the post is clearly positive, neutral, or completely unrelated to the Indian Army, reply NO.

Post caption:
\"\"\"
{caption}
\"\"\"

Reply with exactly one word: YES or NO"""


RESPONSE_PROMPT = """You are a social-media commenter representing the Indian Army's public-relations perspective.

A post with the following caption has been identified as negative toward the Indian Army:

\"\"\"
{caption}
\"\"\"

Write a short, respectful, and patriotic comment that:
- Defends the Indian Army's honour in a dignified way
- Stays factual and measured — never aggressive or abusive
- Encourages the reader to verify claims from official sources
- Is written in the same language as the caption (Hindi, Urdu, or English)
- Is under {max_len} characters (strict limit){exclusion_block}

Reply with ONLY the comment text, nothing else.
"""

EXCLUSION_BLOCK = """

IMPORTANT — the following comments have ALREADY been posted on this exact post by other accounts.
Your comment MUST be completely different in wording, structure, and phrasing. Do NOT reuse any sentence, phrase, or idea from these:
{prior_comments}"""


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def ensure_ollama_server() -> bool:
    """Check if Ollama server is running; if not, attempt to launch it."""
    import subprocess
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434", timeout=2) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    try:
        logging.info("Ollama is not responding — auto-starting 'ollama serve' in background...")
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        with urllib.request.urlopen("http://127.0.0.1:11434", timeout=3) as resp:
            if resp.status == 200:
                logging.info("Ollama server connected successfully.")
                return True
    except Exception as exc:
        logging.warning("Could not auto-start Ollama: %s", exc)
    return False


def is_negative_post(caption: str, model: str) -> bool:
    """Ask Ollama whether the caption is negative toward the Indian Army."""
    for attempt in range(2):
        try:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": SENTIMENT_PROMPT.format(caption=caption)}],
            )
            answer = response["message"]["content"].strip().upper()
            result = answer.startswith("YES")
            logging.info(
                "Sentiment → %s  (raw: %s)",
                "NEGATIVE" if result else "not negative",
                answer[:60],
            )
            return result
        except Exception as exc:
            if attempt == 0 and "connect" in str(exc).lower():
                logging.info("Attempting to connect/launch Ollama server...")
                if ensure_ollama_server():
                    continue
            logging.warning("Ollama sentiment check failed: %s — skipping", exc)
            return False
    return False


def _similarity_ratio(a: str, b: str) -> float:
    """Rough word-overlap ratio between two strings (0.0 – 1.0)."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def generate_response(
    caption: str,
    model: str,
    max_len: int,
    exclude_texts: list[str] | None = None,
    max_uniqueness_attempts: int = 5,
) -> str | None:
    """Use Ollama to draft a contextual reply.

    If *exclude_texts* is provided the prompt explicitly lists previously
    used comments and the result is checked for similarity; generation is
    retried up to *max_uniqueness_attempts* times until a sufficiently
    different candidate is found.
    """
    exclude_texts = [t for t in (exclude_texts or []) if t]

    # Build the exclusion block once for this call
    if exclude_texts:
        prior = "\n".join(f"  - {t}" for t in exclude_texts)
        exclusion_block = EXCLUSION_BLOCK.format(prior_comments=prior)
    else:
        exclusion_block = ""

    prompt = RESPONSE_PROMPT.format(
        caption=caption,
        max_len=max_len,
        exclusion_block=exclusion_block,
    )

    last_exc = None
    for attempt in range(max_uniqueness_attempts):
        try:
            resp = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.85 + attempt * 0.05},  # raise temp on retries
            )
            text = resp["message"]["content"].strip().strip('"').strip("'")
            if not text or len(text) < 10:
                logging.warning("Ollama returned an unusably short response (attempt %d)", attempt + 1)
                continue
            if len(text) > max_len:
                text = text[:max_len].rsplit(" ", 1)[0].rstrip(".,;: ") + "."

            # Uniqueness check — reject if too similar to any prior comment
            if exclude_texts:
                max_sim = max(_similarity_ratio(text, ex) for ex in exclude_texts)
                if max_sim > 0.55:
                    logging.info(
                        "  [unique] Generated comment too similar to a prior one (%.0f%% overlap) — retrying (attempt %d/%d)",
                        max_sim * 100, attempt + 1, max_uniqueness_attempts,
                    )
                    continue

            return text

        except Exception as exc:
            last_exc = exc
            if attempt == 0 and "connect" in str(exc).lower():
                if ensure_ollama_server():
                    continue
            logging.warning("Ollama response generation failed (attempt %d): %s", attempt + 1, exc)

    if last_exc:
        logging.warning("generate_response gave up after %d attempts: %s", max_uniqueness_attempts, last_exc)
    return None



# ---------------------------------------------------------------------------
# Source URL builders
# ---------------------------------------------------------------------------

def hashtag_url(tag: str) -> str:
    return f"https://www.instagram.com/explore/tags/{quote(tag)}/"


def keyword_url(kw: str) -> str:
    return f"https://www.instagram.com/explore/search/keyword/?q={quote(kw)}"


# ---------------------------------------------------------------------------
# Main bot logic — collect + respond in one pass
# ---------------------------------------------------------------------------

def run_bot(
    driver,
    sources: list[tuple[str, str]],
    model: str,
    max_posts_per_source: int,
    max_comments: int,
    delay_seconds: int,
    max_len: int,
    comment_prefix: str,
    max_post_age_days: int = 14,
    my_username: str = "",
    session_comments: dict[str, list[str]] | None = None,
) -> int:
    """Scan sources, detect negatives, generate reply, post comment — with deduplication & trending filters.

    *session_comments* is a shared dict  {media_id: [comment_text, ...]}  that
    accumulates every successfully posted comment across all accounts in the
    session, so each new account gets a prompt that excludes prior ones.
    """
    existing_ids = read_existing_media_ids()
    comments_posted = 0
    if session_comments is None:
        session_comments = {}

    # Ensure active session cookies are preserved before scraping begins
    try:
        _save_cookies(driver, username=my_username)
    except Exception:
        pass

    for label, url in sources:
        if comments_posted >= max_comments:
            logging.info("Reached session comment limit (%d)", max_comments)
            break

        logging.info("Scanning source: %s", label)
        links = collect_post_links(driver, url, max_posts_per_source)
        logging.info("  found %d post link(s)", len(links))

        for post_url in links:
            if comments_posted >= max_comments:
                break

            media_id = extract_media_id(post_url)
            if not media_id:
                continue

            # Check 1: Persistent seen registry and CSV
            if is_post_seen(media_id) or media_id in existing_ids:
                logging.info("  [skip] already processed: %s", media_id)
                continue

            post = extract_post_data(driver, post_url)
            if not post:
                continue

            caption = post["caption"]

            # Check 2: Recency filter (skip stale/old posts)
            age_days = parse_post_age_days(post)
            if age_days is not None and age_days > max_post_age_days:
                logging.info(
                    "  [skip] post too old: %.1f days old (limit: %d days): %s",
                    age_days, max_post_age_days, media_id
                )
                mark_post_seen(media_id, "too_old", post["url"], f"{age_days:.1f} days old")
                existing_ids.add(media_id)
                continue

            # Check 3: Check if account already commented on this post
            if my_username and is_already_commented_by_user(driver, my_username):
                logging.info("  [skip] already commented by @%s on %s", my_username, media_id)
                mark_post_seen(media_id, "already_commented", post["url"])
                existing_ids.add(media_id)
                continue

            # Check 4: Check if negative toward Indian Army
            if not is_negative_post(caption, model):
                logging.info("  [skip] not negative: %s", media_id)
                mark_post_seen(media_id, "not_negative", post["url"])
                existing_ids.add(media_id)
                continue

            age_display = f"{age_days:.1f}d" if age_days is not None else "unknown"
            logging.info("  [NEGATIVE] %s by @%s (age: %s)", media_id, post["username"], age_display)

            # Step 5: Generate response (exclude any comments already posted on this post)
            prior_on_this_post = session_comments.get(media_id, [])
            response_text = generate_response(caption, model, max_len, exclude_texts=prior_on_this_post)
            if not response_text:
                logging.info("  [skip] could not generate response for %s", media_id)
                append_negative_post(NegativePost(
                    media_id=media_id,
                    username=post["username"],
                    permalink=post["url"],
                    caption=caption.replace("\n", " ")[:500],
                    source_tag=label,
                    sentiment="negative",
                    collected_at=datetime.now(timezone.utc).isoformat(),
                    response_status="skipped",
                ))
                mark_post_seen(media_id, "skipped", post["url"], "generation failed")
                existing_ids.add(media_id)
                continue

            # Add prefix if configured
            if comment_prefix:
                response_text = f"{comment_prefix} {response_text}"
                if len(response_text) > max_len:
                    response_text = response_text[:max_len].rsplit(" ", 1)[0].rstrip(".,;: ") + "."

            # Step 6: Post the comment
            success = post_comment(driver, post["url"], response_text)
            status = "posted" if success else "failed"

            # Log to CSVs and persistent seen registry
            append_negative_post(NegativePost(
                media_id=media_id,
                username=post["username"],
                permalink=post["url"],
                caption=caption.replace("\n", " ")[:500],
                source_tag=label,
                sentiment="negative",
                collected_at=datetime.now(timezone.utc).isoformat(),
                response_status=status,
            ))
            append_response_log(ResponseRecord(
                media_id=media_id,
                permalink=post["url"],
                caption_snippet=caption[:120],
                generated_response=response_text,
                status=status,
                responded_at=datetime.now(timezone.utc).isoformat(),
            ))
            mark_post_seen(media_id, status, post["url"])
            existing_ids.add(media_id)

            if success:
                comments_posted += 1
                # Record this comment so subsequent accounts generate something different
                session_comments.setdefault(media_id, []).append(response_text)
                logging.info("  [posted] comment %d/%d on %s", comments_posted, max_comments, media_id)
                try:
                    _save_cookies(driver, username=my_username)
                except Exception:
                    pass
                if comments_posted < max_comments:
                    delay = random.uniform(delay_seconds * 0.8, delay_seconds * 1.3)
                    logging.info("  waiting %.0fs before next action…", delay)
                    time.sleep(delay)
            else:
                logging.warning("  [failed] could not comment on %s", media_id)

    return comments_posted


# ---------------------------------------------------------------------------
# bot_config.json loader
# ---------------------------------------------------------------------------

def _load_bot_config() -> dict:
    """Load dashboard-written bot_config.json; fall back to safe defaults."""
    import json as _json
    cfg_path = ROOT / "bot_config.json"
    defaults = {
        "model": "llama3.2",
        "strategy": "trending_first",
        "max_per_source": 20,
        "max_comments": 12,
        "delay_seconds": 120,
        "max_comment_length": 220,
        "max_age_days": 14,
        "headless": False,
        "comment_prefix": "",
        "enable_trending": True,
        "shuffle_sources": True,
        "enable_vpn_rotation": False,
        "vpn_rotate_command": "",
        "vpn_cooldown_seconds": 8,
        "comments_per_account": 3,
        "neg_hashtags": "indianarmycrimes,armyatrocities,kashmirviolence,humanrightsviolation",
        "pos_hashtags": "indianarmy,indianarmedforces,adgpi,jaihind",
        "keywords": "indian army viral,kashmir encounter,agniveer protest,indian army fake",
    }
    if cfg_path.exists():
        try:
            data = _json.loads(cfg_path.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception as exc:
            logging.warning("Could not read bot_config.json: %s — using defaults", exc)
    return defaults


def main() -> int:
    load_config()
    configure_logging()

    username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    password = os.getenv("INSTAGRAM_PASSWORD", "").strip()

    # Load multi-account list from accounts.txt or fallback to environment variables
    accounts = load_accounts()
    if not accounts and username and password:
        accounts = [(username, password)]

    if not accounts:
        logging.error("No Instagram credentials found. Configure INSTAGRAM_USERNAME/PASSWORD or accounts.txt")
        return 2

    cfg = _load_bot_config()
    model            = cfg["model"]
    strategy         = cfg["strategy"].lower()
    max_posts        = int(cfg["max_per_source"])
    max_comments     = int(cfg["max_comments"])
    delay_seconds    = int(cfg["delay_seconds"])
    max_len          = int(cfg["max_comment_length"])
    max_post_age_days = int(cfg["max_age_days"])
    headless         = bool(cfg["headless"])
    comment_prefix   = cfg["comment_prefix"]
    enable_trending_news = bool(cfg["enable_trending"])
    shuffle_sources  = bool(cfg["shuffle_sources"])
    enable_vpn       = bool(cfg.get("enable_vpn_rotation", False))
    vpn_cmd          = str(cfg.get("vpn_rotate_command", "")).strip()
    vpn_cooldown     = int(cfg.get("vpn_cooldown_seconds", 8))
    comments_per_account = int(cfg.get("comments_per_account", 3))
    account_cooldown_min = int(cfg.get("account_cooldown_minutes", 15))

    def _split(val: str) -> list[str]:
        return [t.strip().lstrip("#") for t in str(val).split(",") if t.strip()]

    neg_tags = _split(cfg["neg_hashtags"])
    pos_tags = _split(cfg["pos_hashtags"])
    keywords = _split(cfg["keywords"])

    # Live trending topics from RSS
    trending_sources: list[tuple[str, str]] = []
    if enable_trending_news:
        try:
            live_topics = fetch_trending_topics(max_topics=5)
            if live_topics:
                logging.info("Fetched %d live trending military topic(s): %s", len(live_topics), ", ".join(live_topics))
                trending_sources = [(f"trend:{topic}", keyword_url(topic)) for topic in live_topics]
        except Exception as exc:
            logging.debug("Could not fetch live trending topics: %s", exc)

    keyword_sources = [(f"kw:{kw}", keyword_url(kw)) for kw in keywords]
    neg_sources = [(f"#{t}", hashtag_url(t)) for t in neg_tags]
    pos_sources = [(f"#{t}", hashtag_url(t)) for t in pos_tags]

    sources: list[tuple[str, str]] = []
    if strategy == "trending_first":
        if shuffle_sources:
            random.shuffle(trending_sources)
            random.shuffle(keyword_sources)
        sources = trending_sources + keyword_sources + neg_sources + pos_sources
    elif strategy == "negative_first":
        sources = neg_sources + pos_sources + keyword_sources + trending_sources
    elif strategy == "positive_only":
        sources = pos_sources
    else:  # balanced
        sources = trending_sources + keyword_sources + neg_sources + pos_sources
        if shuffle_sources:
            random.shuffle(sources)

    if not sources:
        logging.error("No hashtags or keywords configured — nothing to scan")
        return 2

    logging.info(
        "Bot starting — %d account(s), %d source(s), max %d comment(s) total (%d/account), VPN rotation=%s, model=%s",
        len(accounts), len(sources), max_comments, comments_per_account, enable_vpn, model
    )

    ensure_ollama_server()

    total_comments_posted = 0
    account_idx = 0
    # Shared across all account sessions: {media_id: [comment_text, ...]} to enforce uniqueness
    session_comments: dict[str, list[str]] = {}

    while total_comments_posted < max_comments and account_idx < len(accounts):
        acct_user, acct_pwd = accounts[account_idx]
        remaining_budget = min(comments_per_account, max_comments - total_comments_posted)

        # Optimization: Check if account is in safety cooldown period
        is_cooling, rem_sec = is_account_cooling(acct_user, cooldown_minutes=account_cooldown_min)
        if is_cooling and len(accounts) > 1:
            logging.info("Account @%s in safety cooldown (%ds remaining) — cycling to next ID", acct_user, rem_sec)
            account_idx += 1
            continue

        logging.info(
            "══════════════════════════════════════════════════════════════\n"
            "  SWITCHING ACCOUNT [%d/%d]: @%s (budget: %d comment(s))\n"
            "══════════════════════════════════════════════════════════════",
            account_idx + 1, len(accounts), acct_user, remaining_budget
        )

        current_ip = "UNKNOWN"
        current_loc = ""
        if enable_vpn:
            logging.info("[Multi-Account] Rotating VPN network connection before launching @%s...", acct_user)
            vpn_res = rotate_vpn(command=vpn_cmd, cooldown_seconds=vpn_cooldown)
            current_ip = str(vpn_res.get("new_ip", "UNKNOWN"))
            current_loc = str(vpn_res.get("location", ""))
            logging.info("[Multi-Account] %s", vpn_res.get("message", "VPN rotation finished"))
        else:
            net_info = get_ip_details(timeout=2.5)
            current_ip = str(net_info.get("ip", "UNKNOWN"))
            current_loc = str(net_info.get("summary", ""))

        record_session_start(acct_user, ip=current_ip, location=current_loc)

        p_cookie, a_cookie = get_cookies_file(acct_user)
        cookies_exist = p_cookie.exists() or a_cookie.exists()
        is_headless = headless and cookies_exist

        if not is_headless:
            logging.info("Opening visible Chrome browser with dedicated profile for @%s...", acct_user)
        else:
            logging.info("Starting Chrome in headless mode for @%s (dedicated profile ready)...", acct_user)

        driver = None
        posted = 0
        try:
            # Dedicated virtual browser profile per account to prevent device fingerprint flagging
            driver = create_driver(is_headless, username=acct_user)
            if not login_instagram(driver, acct_user, acct_pwd):
                logging.warning("Skipping @%s due to login/checkpoint challenge", acct_user)
                record_session_end(acct_user, comments_posted=0, status="challenge_required", error="Authentication challenge")
                account_idx += 1
                continue

            try:
                _save_cookies(driver, username=acct_user)
            except Exception:
                pass

            posted = run_bot(
                driver,
                sources,
                model,
                max_posts,
                remaining_budget,
                delay_seconds,
                max_len,
                comment_prefix,
                max_post_age_days=max_post_age_days,
                my_username=acct_user,
                session_comments=session_comments,  # share across accounts for uniqueness
            )
            total_comments_posted += posted
            record_session_end(acct_user, comments_posted=posted, status="cooling" if posted > 0 else "ready")
            logging.info("Account @%s session completed — posted %d comment(s)", acct_user, posted)

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

        account_idx += 1
        if total_comments_posted < max_comments and account_idx < len(accounts):
            logging.info("Inter-account pacing: waiting 10s before launching next profile...")
            time.sleep(10)

    logging.info("All bot sessions completed — posted %d total comment(s)", total_comments_posted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
