from __future__ import annotations

import logging
from typing import Any

from analyzer import analyze_caption
from generator import generate_response
from verifier import verify_claims
from shared import (
    create_driver,
    extract_post_data,
    load_config,
    login_instagram,
    post_comment,
)


def analyze_post_content(
    post: dict[str, Any],
    model: str,
    driver: Any = None,
) -> dict[str, Any]:
    """Analyze already extracted post dictionary (caption, ocr_text, url, username)."""
    caption = post.get("caption", "")
    ocr_text = post.get("ocr_text", "").strip()
    analysis_input = f"{caption}\n\n[Extracted Text from Image/Meme]:\n{ocr_text}" if ocr_text else caption

    # 1. Ollama analysis
    analysis = analyze_caption(
        caption=analysis_input,
        model=model,
    )

    # 2. Ignore unrelated posts
    if analysis["category"] != "ARMY_RELATED":
        return {
            "post": post,
            "analysis": analysis,
            "verification": [],
            "response": "",
            "status": "NOT_ARMY_RELATED",
            "approval_status": "SKIPPED",
        }

    # 3. Government-source verification (RAG & Web Search)
    verification = verify_claims(
        driver=driver,
        claims=analysis.get("claims", []),
        model=model,
    )

    # 4. Generate grounded draft
    response = generate_response(
        caption=caption,
        analysis=analysis,
        verification_results=verification,
        model=model,
    )

    return {
        "post": post,
        "analysis": analysis,
        "verification": verification,
        "response": response,
        "status": "READY_FOR_REVIEW",
        "approval_status": "PENDING",
    }


def analyze_instagram_url(
    instagram_url: str,
    model: str,
    headless: bool = False,
) -> dict[str, Any]:
    """Complete analysis pipeline for a single Instagram URL via Selenium."""
    driver = create_driver(headless=headless)
    try:
        post = extract_post_data(driver, instagram_url)
        if not post:
            raise RuntimeError(
                f"Could not extract data from {instagram_url}. "
                "Ensure the post is public or cookies are valid."
            )
        return analyze_post_content(post=post, model=model, driver=driver)
    except Exception as exc:
        logging.exception("Pipeline failed on %s: %s", instagram_url, exc)
        raise
    finally:
        driver.quit()


def publish_approved_comment(
    post_url: str,
    comment_text: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Publish an approved comment to Instagram using the configured official account."""
    if dry_run:
        logging.info("[DRY RUN] Would post to %s: %s", post_url, comment_text)
        return True, "Simulation Successful: Comment validated and logged in dry-run mode."

    config = load_config()
    username = config.get("username", "")
    password = config.get("password", "")

    if not username or not password:
        return False, "Instagram credentials missing. Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env."

    driver = create_driver(headless=config.get("headless", False))
    try:
        logged_in = login_instagram(driver, username, password)
        if not logged_in:
            return False, "Failed to authenticate with Instagram. Check credentials or OTP challenge in browser."

        success = post_comment(driver, post_url, comment_text)
        if success:
            return True, f"Comment successfully posted to Instagram from official account @{username}."
        else:
            return False, "Comment textarea could not be submitted on Instagram."
    except Exception as exc:
        logging.exception("Failed to publish comment: %s", exc)
        return False, f"Exception occurred while posting: {exc}"
    finally:
        driver.quit()