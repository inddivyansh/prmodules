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
    NegativePost,
    ResponseRecord,
    append_negative_post,
    append_response_log,
    bounded_int,
    collect_post_links,
    configure_logging,
    create_driver,
    env_list,
    extract_post_data,
    load_config,
    login_instagram,
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
) -> int:
    """Scan sources, detect negatives, generate reply, post comment — one pass."""
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

            post = extract_post_data(driver, post_url)
            if not post:
                continue
            if post["media_id"] in existing_ids:
                logging.info("  [skip] already processed: %s", post["media_id"])
                continue

            caption = post["caption"]

            # Step 1: Check if negative
            if not is_negative_post(caption, model):
                logging.info("  [skip] not negative: %s", post["media_id"])
                continue

            logging.info("  [NEGATIVE] %s by @%s", post["media_id"], post["username"])

            # Step 2: Generate response
            response_text = generate_response(caption, model, max_len)
            if not response_text:
                logging.info("  [skip] could not generate response for %s", post["media_id"])
                # Still log it as collected but skipped
                append_negative_post(NegativePost(
                    media_id=post["media_id"],
                    username=post["username"],
                    permalink=post["url"],
                    caption=caption.replace("\n", " ")[:500],
                    source_tag=label,
                    sentiment="negative",
                    collected_at=datetime.now(timezone.utc).isoformat(),
                    response_status="skipped",
                ))
                existing_ids.add(post["media_id"])
                continue

            # Add prefix if configured
            if comment_prefix:
                response_text = f"{comment_prefix} {response_text}"
                if len(response_text) > max_len:
                    response_text = response_text[:max_len].rsplit(" ", 1)[0].rstrip(".,;: ") + "."

            # Step 3: Post the comment
            success = post_comment(driver, post["url"], response_text)
            status = "posted" if success else "failed"

            # Log to both CSVs
            append_negative_post(NegativePost(
                media_id=post["media_id"],
                username=post["username"],
                permalink=post["url"],
                caption=caption.replace("\n", " ")[:500],
                source_tag=label,
                sentiment="negative",
                collected_at=datetime.now(timezone.utc).isoformat(),
                response_status=status,
            ))
            append_response_log(ResponseRecord(
                media_id=post["media_id"],
                permalink=post["url"],
                caption_snippet=caption[:120],
                generated_response=response_text,
                status=status,
                responded_at=datetime.now(timezone.utc).isoformat(),
            ))
            existing_ids.add(post["media_id"])

            if success:
                comments_posted += 1
                logging.info("  ✅ [posted] comment %d/%d on %s", comments_posted, max_comments, post["media_id"])
                if comments_posted < max_comments:
                    delay = random.uniform(delay_seconds * 0.8, delay_seconds * 1.3)
                    logging.info("  waiting %.0fs before next action…", delay)
                    time.sleep(delay)
            else:
                logging.warning("  ❌ [failed] could not comment on %s", post["media_id"])

    return comments_posted


def main() -> int:
    load_config()
    configure_logging()

    username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    password = os.getenv("INSTAGRAM_PASSWORD", "").strip()
    if not username or not password:
        logging.error("INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD are required")
        return 2

    model = os.getenv("OLLAMA_MODEL", "llama3.2").strip()
    strategy = os.getenv("DETECTION_STRATEGY", "negative_first").strip().lower()
    max_posts = bounded_int("MAX_POSTS_TO_SCAN_PER_SOURCE", 20, 1, 100)
    max_comments = bounded_int("MAX_COMMENTS_PER_SESSION", 6, 1, 15)
    delay_seconds = bounded_int("DELAY_BETWEEN_COMMENTS_SECONDS", 90, 30, 3600)
    max_len = bounded_int("MAX_COMMENT_LENGTH", 220, 50, 500)
    headless = os.getenv("HEADLESS", "false").strip().lower() == "true"
    comment_prefix = os.getenv("COMMENT_PREFIX", "").strip()

    # Build source list based on strategy
    neg_tags = env_list("NEGATIVE_HASHTAGS")
    pos_tags = env_list("POSITIVE_HASHTAGS")
    keywords = env_list("SEARCH_KEYWORDS")
    if not neg_tags and not pos_tags:
        all_tags = env_list("HASHTAGS", "MONITOR_HASHTAGS")
        neg_tags = all_tags

    sources: list[tuple[str, str]] = []
    if strategy == "negative_first":
        sources += [(f"#{t}", hashtag_url(t)) for t in neg_tags]
        sources += [(f"#{t}", hashtag_url(t)) for t in pos_tags]
    elif strategy == "positive_only":
        sources += [(f"#{t}", hashtag_url(t)) for t in pos_tags]
    else:
        combined = neg_tags + pos_tags
        sources += [(f"#{t}", hashtag_url(t)) for t in combined]
    sources += [(f"kw:{kw}", keyword_url(kw)) for kw in keywords]

    if not sources:
        logging.error("No hashtags or keywords configured — nothing to scan")
        return 2

    logging.info("Bot starting — %d source(s), max %d comment(s), model=%s",
                 len(sources), max_comments, model)

    driver = None
    try:
        driver = create_driver(headless)
        if not login_instagram(driver, username, password):
            return 1
        total = run_bot(
            driver, sources, model, max_posts, max_comments,
            delay_seconds, max_len, comment_prefix,
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
