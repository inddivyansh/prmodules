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
    COOKIES_FILE,
    ROOT,
    NegativePost,
    ResponseRecord,
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
    is_already_commented_by_user,
    is_post_seen,
    load_config,
    login_instagram,
    mark_post_seen,
    parse_post_age_days,
    pause,
    post_comment,
    read_existing_media_ids,
    update_post_status,
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
- Is under {max_len} characters (strict limit)

Reply with ONLY the comment text, nothing else.
"""


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def is_negative_post(caption: str, model: str) -> bool:
    """Ask Ollama whether the caption is negative toward the Indian Army."""
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
        logging.warning("Ollama sentiment check failed: %s — skipping", exc)
        return False


def generate_response(caption: str, model: str, max_len: int) -> str | None:
    """Use Ollama to draft a contextual reply."""
    try:
        resp = ollama.chat(
            model=model,
            messages=[{
                "role": "user",
                "content": RESPONSE_PROMPT.format(caption=caption, max_len=max_len),
            }],
        )
        text = resp["message"]["content"].strip().strip('"').strip("'")
        if not text or len(text) < 10:
            logging.warning("Ollama returned an unusably short response")
            return None
        if len(text) > max_len:
            text = text[:max_len].rsplit(" ", 1)[0].rstrip(".,;: ") + "."
        return text
    except Exception as exc:
        logging.warning("Ollama response generation failed: %s", exc)
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
) -> int:
    """Scan sources, detect negatives, generate reply, post comment — with deduplication & trending filters."""
    existing_ids = read_existing_media_ids()
    comments_posted = 0

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

            # Step 5: Generate response
            response_text = generate_response(caption, model, max_len)
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
                logging.info("  ✅ [posted] comment %d/%d on %s", comments_posted, max_comments, media_id)
                if comments_posted < max_comments:
                    delay = random.uniform(delay_seconds * 0.8, delay_seconds * 1.3)
                    logging.info("  waiting %.0fs before next action…", delay)
                    time.sleep(delay)
            else:
                logging.warning("  ❌ [failed] could not comment on %s", media_id)

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
    if not username or not password:
        logging.error("INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD are required")
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

    logging.info("Bot starting — %d source(s), max %d comment(s), max_age=%dd, model=%s",
                 len(sources), max_comments, max_post_age_days, model)

    driver = None
    try:
        # Never hide browser during login if session cookies are absent,
        # or if headless is set to False, so the user can complete human verification / 2FA.
        is_headless = headless and COOKIES_FILE.exists()
        if not is_headless:
            logging.info("Opening visible automated Chrome browser for Instagram session (human verification ready)...")
        else:
            logging.info("Starting Chrome in headless mode (session cookies present)...")
        driver = create_driver(is_headless)
        if not login_instagram(driver, username, password):
            return 1
        total = run_bot(
            driver,
            sources,
            model,
            max_posts,
            max_comments,
            delay_seconds,
            max_len,
            comment_prefix,
            max_post_age_days=max_post_age_days,
            my_username=username,
        )
        logging.info("Bot finished — posted %d comment(s)", total)
        return 0
    except Exception as exc:
        logging.error("Bot crashed: %s", exc)
        return 1
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    sys.exit(main())
