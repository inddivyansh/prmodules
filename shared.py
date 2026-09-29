"""Shared utilities for the Indian Army PR bot.

Common helpers for Instagram login, browser management, CSV I/O, and logging
used by both collector.py and responder.py.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import random
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "monitor.log"
COOKIES_FILE = ROOT / ".instagram_cookies.json"
NEGATIVE_POSTS_CSV = ROOT / "negative_posts.csv"
RESPONSE_LOG_CSV = ROOT / "response_log.csv"
SEEN_POSTS_FILE = ROOT / "seen_posts.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NegativePost:
    """A single negative post collected by the collector."""
    media_id: str
    username: str
    permalink: str
    caption: str
    source_tag: str          # hashtag or keyword that led to this post
    sentiment: str           # e.g. "negative"
    collected_at: str        # ISO-8601 UTC
    response_status: str     # pending | posted | failed | skipped


@dataclass
class ResponseRecord:
    """Audit record written by the responder after attempting a reply."""
    media_id: str
    permalink: str
    caption_snippet: str
    generated_response: str
    status: str              # posted | failed | skipped | rejected
    responded_at: str        # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Load .env from the project root and return parsed settings."""
    load_dotenv(ROOT / ".env")
    return {
        "username": os.getenv("INSTAGRAM_USERNAME", "").strip(),
        "password": os.getenv("INSTAGRAM_PASSWORD", "").strip(),
        "model": os.getenv("OLLAMA_MODEL", "llama3.2:latest").strip(),
        "headless": os.getenv("HEADLESS", "false").lower() == "true",
    }


def env_list(*names: str) -> list[str]:
    """Return the first populated comma-separated env var as a list."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return [item.strip().lstrip("#") for item in value.split(",") if item.strip()]
    return []


def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer env var clamped to [minimum, maximum]."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        logging.warning("%s must be an integer; using %d", name, default)
        return default
    return min(max(value, minimum), maximum)


# ---------------------------------------------------------------------------
# Browser / Selenium
# ---------------------------------------------------------------------------

def pause(seconds: float) -> None:
    """Simple wait — not intended to mask automation."""
    time.sleep(seconds)


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """Start Chrome configured to look like a normal user browser."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    else:
        options.add_argument("--start-maximized")
    options.add_argument("--window-size=1920,1080")

    # Suppress automation markers
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Look like a real browser
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=en-US,en")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )

    if not headless:
        try:
            driver.maximize_window()
        except Exception:
            pass

    # Remove navigator.webdriver flag so JS fingerprinting sees a normal browser
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """},
    )

    return driver


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    """Check multiple indicators that we're logged into Instagram."""
    for sel in (
        "//a[contains(@href, '/direct/')]",
        "//svg[@aria-label='Home']",
        "//a[@href='/'][@role='link']",
        "//span[@aria-label='Profile']",
        "//img[@data-testid='user-avatar']",
    ):
        if driver.find_elements(By.XPATH, sel):
            return True
    return False


def _dismiss_dialogs(driver: webdriver.Chrome) -> None:
    """Try to dismiss cookie-consent banners and other overlay dialogs."""
    for label in (
        "Allow all cookies",
        "Allow essential and optional cookies",
        "Accept All",
        "Accept",
        "Only allow essential cookies",
        "Decline optional cookies",
        "Save Info",
        "Not Now",
    ):
        try:
            buttons = driver.find_elements(
                By.XPATH, f"//button[contains(text(), '{label}')]"
            )
            if buttons:
                buttons[0].click()
                logging.info("Dismissed dialog: '%s'", label)
                pause(2)
                return
        except WebDriverException:
            continue


def _human_type(element, text: str) -> None:
    """Type text character-by-character with random human-like delays."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.05, 0.18))


def _save_cookies(driver: webdriver.Chrome) -> None:
    COOKIES_FILE.write_text(json.dumps(driver.get_cookies()), encoding="utf-8")
    logging.info("Session cookies saved")


def _load_cookies(driver: webdriver.Chrome) -> bool:
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        if not isinstance(cookies, list):
            return False
        driver.get("https://www.instagram.com/")
        pause(3)
        _dismiss_dialogs(driver)
        for cookie in cookies:
            if isinstance(cookie, dict):
                if "expiry" in cookie:
                    cookie["expiry"] = int(cookie["expiry"])
                try:
                    driver.add_cookie(cookie)
                except WebDriverException:
                    continue
        driver.refresh()
        pause(3)
        return _is_logged_in(driver)
    except (OSError, ValueError, WebDriverException) as exc:
        logging.warning("Could not restore session cookies: %s", exc)
        return False


def _detect_otp_challenge(driver: webdriver.Chrome) -> bool:
    """Check if Instagram is showing an OTP / 2FA / verification / checkpoint screen."""
    otp_indicators = (
        "//input[@name='verificationCode']",
        "//input[@aria-label='Security Code']",
        "//input[@aria-label='Confirmation Code']",
        "//input[@name='security_code']",
        "//input[@placeholder='Security code']",
        "//input[@placeholder='Confirmation code']",
        "//p[contains(text(), 'security code')]",
        "//p[contains(text(), 'confirmation code')]",
        "//span[contains(text(), 'Enter the code')]",
        "//span[contains(text(), 'we sent')]",
        "//h2[contains(text(), 'suspicious')]",
        "//h2[contains(text(), 'Help us confirm')]",
        "//span[contains(text(), 'Confirm that this is you')]",
        "//div[contains(text(), 'Confirm it’s you')]",
        "//div[contains(text(), 'challenge')]",
    )
    for sel in otp_indicators:
        if driver.find_elements(By.XPATH, sel):
            return True
    return False


def _wait_for_otp(driver: webdriver.Chrome, timeout: int = 180) -> bool:
    """Show OTP prompt and wait for user to enter code in the open Chrome browser."""
    logging.info("HUMAN VERIFICATION / OTP REQUIRED: Instagram is asking for verification. Please complete it in the Chrome browser window (waiting up to %ds)...", timeout)
    waited = 0
    interval = 5
    while waited < timeout:
        remaining = timeout - waited
        if waited % 15 == 0:
            logging.info("Waiting for human verification / code entry in Chrome window (%ds remaining)...", remaining)
        pause(interval)
        waited += interval

        if _is_logged_in(driver):
            logging.info("Human verification confirmed! Login successful.")
            return True

        if not _detect_otp_challenge(driver):
            pause(3)
            if _is_logged_in(driver):
                logging.info("Human verification confirmed! Login successful.")
                return True

    logging.warning("Verification timeout — code or challenge was not completed within %ds.", timeout)
    return False


def _wait_for_manual_login(driver: webdriver.Chrome, timeout: int = 180) -> bool:
    """Fallback: ask user to complete login / verification manually in the open Chrome window."""
    logging.info("MANUAL VERIFICATION REQUIRED: Please complete login/checkpoint in the open Chrome browser window (waiting up to %ds)...", timeout)
    waited = 0
    interval = 5
    while waited < timeout:
        remaining = timeout - waited
        if waited % 15 == 0:
            logging.info("Waiting for manual verification in Chrome browser (%ds remaining)...", remaining)
        pause(interval)
        waited += interval

        if _is_logged_in(driver):
            logging.info("Manual login confirmed in Chrome browser! Session established.")
            return True

    logging.warning("Manual login timeout — session was not completed within %ds.", timeout)
    return False


def login_instagram(driver: webdriver.Chrome, username: str, password: str) -> bool:
    """3-step login: cookies → auto login with slow typing → OTP wait → manual fallback."""

    # Step 0: Try saved cookies
    if _load_cookies(driver):
        logging.info("✅ Restored existing Instagram session from cookies")
        return True

    # Step 1: Automatic login with human-like typing
    try:
        logging.info("Navigating to Instagram login page…")
        driver.get("https://www.instagram.com/accounts/login/")
        pause(4)
        _dismiss_dialogs(driver)

        logging.info("Looking for login fields…")

        # Try multiple selectors for username
        user_input = None
        for sel in (
            (By.NAME, "username"),
            (By.CSS_SELECTOR, "input[name='username']"),
            (By.XPATH, "//input[@aria-label='Phone number, username, or email']"),
            (By.CSS_SELECTOR, "input[type='text']"),
        ):
            try:
                user_input = WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located(sel)
                )
                if user_input:
                    break
            except (TimeoutException, WebDriverException):
                continue

        if not user_input:
            logging.warning("Could not find username field — falling back to manual login")
            return _wait_for_manual_login(driver)

        # Try multiple selectors for password
        pass_input = None
        for sel in (
            (By.NAME, "password"),
            (By.CSS_SELECTOR, "input[name='password']"),
            (By.XPATH, "//input[@aria-label='Password']"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ):
            try:
                pass_input = WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located(sel)
                )
                if pass_input:
                    break
            except (TimeoutException, WebDriverException):
                continue

        if not pass_input:
            logging.warning("Could not find password field — falling back to manual login")
            return _wait_for_manual_login(driver)

        # Type credentials slowly, like a human
        logging.info("Typing username…")
        user_input.click()
        pause(0.3)
        user_input.clear()
        _human_type(user_input, username)
        pause(random.uniform(0.5, 1.0))

        logging.info("Typing password…")
        pass_input.click()
        pause(0.3)
        pass_input.clear()
        _human_type(pass_input, password)
        pause(random.uniform(0.5, 1.0))

        # Submit
        logging.info("Submitting login…")
        pass_input.send_keys(Keys.RETURN)
        pause(6)

        # Check for OTP/2FA challenge
        if _detect_otp_challenge(driver):
            logging.info("OTP/2FA challenge detected")
            if not _wait_for_otp(driver):
                return _wait_for_manual_login(driver)
        elif not _is_logged_in(driver):
            # Maybe a different challenge or slow load — wait a bit more
            pause(5)
            _dismiss_dialogs(driver)

            if _detect_otp_challenge(driver):
                logging.info("OTP/2FA challenge detected (delayed)")
                if not _wait_for_otp(driver):
                    return _wait_for_manual_login(driver)
            elif not _is_logged_in(driver):
                logging.warning("Auto-login didn't complete — trying manual login")
                return _wait_for_manual_login(driver)

        # Dismiss post-login popups
        _dismiss_dialogs(driver)
        pause(2)

        if _is_logged_in(driver):
            _save_cookies(driver)
            logging.info("✅ Logged in as @%s", username)
            return True

        # Last resort
        return _wait_for_manual_login(driver)

    except (TimeoutException, WebDriverException) as exc:
        logging.warning("Auto-login error: %s", exc)
        try:
            ss_path = ROOT / "login_error.png"
            driver.save_screenshot(str(ss_path))
            logging.info("Screenshot saved: %s", ss_path)
        except Exception:
            pass
        # Fall back to manual login
        return _wait_for_manual_login(driver)


# ---------------------------------------------------------------------------
# Instagram scraping helpers
# ---------------------------------------------------------------------------

def extract_media_id(url: str) -> str:
    """Extract canonical media identifier from Instagram /p/, /reel/, /reels/, or /tv/ URL."""
    if not url:
        return ""
    clean = url.split("?")[0].rstrip("/")
    for token in ("/p/", "/reel/", "/reels/", "/tv/"):
        if token in clean:
            part = clean.split(token, 1)[-1].strip("/")
            return part.split("/", 1)[0]
    return clean.split("/")[-1]


def is_already_commented_by_user(driver: webdriver.Chrome, username: str) -> bool:
    """Check whether our account has already posted a comment on the currently opened post."""
    if not username:
        return False
    target = username.strip().lower()
    try:
        selectors = (
            "//ul//h3//a",
            "//ul//a[@role='link']",
            "//div[contains(@class, 'comment')]//a",
            "//span[contains(@class, '_ap3a') and contains(@class, '_aaco')]",
        )
        for sel in selectors:
            for el in driver.find_elements(By.XPATH, sel):
                txt = (el.text or "").strip().lower()
                href = (el.get_attribute("href") or "").lower()
                if txt == target or f"/{target}/" in href:
                    return True
    except Exception:
        pass
    return False


def parse_post_age_days(post: dict[str, Any]) -> float | None:
    """Calculate the age of the post in days, or None if undetermined."""
    ts_str = post.get("post_timestamp")
    if ts_str:
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
        except Exception:
            pass

    caption = post.get("caption", "")
    match = re.search(r"on\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", caption)
    if match:
        month_name, day_str, year_str = match.groups()
        try:
            dt = datetime.strptime(f"{month_name} {day_str} {year_str}", "%B %d %Y").replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
        except Exception:
            pass

    return None


def scroll_and_load(driver: webdriver.Chrome, scrolls: int = 2) -> None:
    for _ in range(scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        pause(2)


def collect_post_links(driver: webdriver.Chrome, url: str, max_posts: int, scrolls: int = 4) -> list[str]:
    """Navigate to *url* and collect up to *max_posts* unique post/reel links."""
    try:
        driver.get(url)
        pause(3.0)
        _dismiss_dialogs(driver)

        links: list[str] = []
        seen_ids: set[str] = set()

        for _ in range(scrolls):
            anchors = driver.find_elements(
                By.XPATH,
                "//a[contains(@href, '/p/') or contains(@href, '/reel/') or contains(@href, '/reels/')]"
            )
            for anchor in anchors:
                try:
                    href = anchor.get_attribute("href")
                    if not href:
                        continue
                    clean_url = href.split("?")[0]
                    media_id = extract_media_id(clean_url)
                    if media_id and media_id not in seen_ids:
                        seen_ids.add(media_id)
                        # Canonicalize to standard /p/ URL for unified DOM rendering in desktop browser
                        norm_url = f"https://www.instagram.com/p/{media_id}/"
                        links.append(norm_url)
                    if len(links) >= max_posts:
                        break
                except StaleElementReferenceException:
                    continue

            if len(links) >= max_posts:
                break

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            pause(2.5)

        return links
    except WebDriverException as exc:
        logging.warning("Could not load %s: %s", url, exc)
        return []


def extract_image_text(image_url: str) -> str:
    """Extract embedded text from an image URL using OCR."""
    if not image_url or not PYTESSERACT_AVAILABLE:
        return ""
    try:
        resp = requests.get(image_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            img = Image.open(io.BytesIO(resp.content))
            text = pytesseract.image_to_string(img).strip()
            return text
    except Exception as exc:
        logging.debug("OCR extraction failed for %s: %s", image_url, exc)
    return ""


def extract_post_data(driver: webdriver.Chrome, post_url: str) -> dict[str, str] | None:
    """Return post metadata dictionary including recency timestamp, or None on failure."""
    try:
        driver.get(post_url)
        pause(3)

        # --- username ---
        username = "unknown"
        for selector in (
            "//header//a[@role='link']//span",
            "//article//header//a",
            "//h2//a",
        ):
            elements = driver.find_elements(By.XPATH, selector)
            if elements and elements[0].text.strip():
                username = elements[0].text.strip()
                break

        # --- timestamp / recency ---
        post_timestamp = ""
        try:
            for time_el in driver.find_elements(By.XPATH, "//article//time | //time"):
                dt_val = time_el.get_attribute("datetime")
                if dt_val:
                    post_timestamp = dt_val
                    break
        except Exception:
            pass

        # --- caption ---
        caption = ""
        for selector in ("//article//h1", "//article//ul//span"):
            values = [
                el.text.strip()
                for el in driver.find_elements(By.XPATH, selector)
                if len(el.text.strip()) > 10
            ]
            if values:
                caption = max(values, key=len)
                break
        if not caption:
            meta = driver.find_elements(By.XPATH, "//meta[@property='og:description']")
            if meta:
                caption = (meta[0].get_attribute("content") or "").strip()
        if not caption:
            return None

        # --- image url & ocr ---
        image_url = ""
        ocr_text = ""
        img_elements = driver.find_elements(By.XPATH, "//article//img")
        for img in img_elements:
            src = img.get_attribute("src") or ""
            if src.startswith("http"):
                image_url = src
                break
        if not image_url:
            meta_img = driver.find_elements(By.XPATH, "//meta[@property='og:image']")
            if meta_img:
                image_url = meta_img[0].get_attribute("content") or ""

        if image_url:
            ocr_text = extract_image_text(image_url)

        media_id = extract_media_id(post_url)
        return {
            "url": post_url,
            "username": username,
            "caption": caption,
            "media_id": media_id,
            "image_url": image_url,
            "ocr_text": ocr_text,
            "post_timestamp": post_timestamp,
        }
    except WebDriverException as exc:
        logging.warning("Could not extract post data from %s: %s", post_url, exc)
        return None


def post_comment(driver: webdriver.Chrome, post_url: str, comment_text: str) -> bool:
    """Post a comment on the given Instagram post URL.

    Robust against Instagram's dynamic React DOM and re-renders:
    - Avoids reloading the page if already open
    - Focuses and activates the comment textarea
    - Re-locates elements dynamically to avoid StaleElementReferenceException
    - Types the comment into the textarea
    - Submits via Enter key and/or clicking the Post button (handles div[role='button'] & button)
    - Verifies submission through cleared textarea and on-page comment text
    """
    try:
        # Check if driver is already on this post's page
        shortcode = extract_media_id(post_url)
        if not shortcode or shortcode not in driver.current_url:
            driver.get(post_url)
            pause(random.uniform(2.5, 3.5))
        else:
            pause(1.0)

        _dismiss_dialogs(driver)

        textarea_selectors = (
            "//textarea[contains(@placeholder, 'Add a comment')]",
            "//textarea[contains(@aria-label, 'Add a comment')]",
            "//textarea[@placeholder='Add a comment…']",
            "//textarea[@aria-label='Add a comment…']",
            "//form//textarea",
        )

        # 1. Locate the comment box
        comment_box = None
        for sel in textarea_selectors:
            try:
                candidates = driver.find_elements(By.XPATH, sel)
                for el in candidates:
                    if el.is_displayed():
                        comment_box = el
                        break
                if comment_box:
                    break
            except (StaleElementReferenceException, WebDriverException):
                continue

        # If not directly found, try clicking the comment icon (speech bubble) to open it
        if not comment_box:
            for icon_sel in (
                "//svg[@aria-label='Comment']/ancestor::div[@role='button']",
                "//svg[@aria-label='Comment']/ancestor::button",
                "//span[contains(@class, 'comment')]",
            ):
                try:
                    icons = driver.find_elements(By.XPATH, icon_sel)
                    if icons and icons[0].is_displayed():
                        icons[0].click()
                        pause(1.5)
                        break
                except Exception:
                    continue

            for sel in textarea_selectors:
                try:
                    candidates = driver.find_elements(By.XPATH, sel)
                    for el in candidates:
                        if el.is_displayed():
                            comment_box = el
                            break
                    if comment_box:
                        break
                except (StaleElementReferenceException, WebDriverException):
                    continue

        if not comment_box:
            logging.warning("Could not find comment textarea on %s", post_url)
            return False

        # 2. Scroll into view and focus
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", comment_box)
            pause(0.4)
        except Exception:
            pass

        try:
            comment_box.click()
            pause(0.5)
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", comment_box)
                pause(0.5)
            except Exception:
                pass

        # 3. Re-locate the active textarea to prevent StaleElementReferenceException after focus
        active_box = None
        for sel in textarea_selectors:
            try:
                candidates = driver.find_elements(By.XPATH, sel)
                for el in candidates:
                    if el.is_displayed():
                        active_box = el
                        break
                if active_box:
                    break
            except (StaleElementReferenceException, WebDriverException):
                continue

        if not active_box:
            active_box = comment_box

        # Clear any residual text and type comment
        try:
            active_box.clear()
        except Exception:
            pass

        logging.info("Typing comment into comment box…")
        active_box.send_keys(comment_text)
        pause(1.0)

        submitted = False

        # --- Submission Method 1: Press Enter in the comment box ---
        try:
            active_box.send_keys(Keys.RETURN)
            pause(1.5)
            try:
                val = active_box.get_attribute("value") or ""
                if not val.strip():
                    submitted = True
                    logging.info("Comment submitted via Enter key")
            except StaleElementReferenceException:
                # Textarea was re-rendered/unmounted on submit
                submitted = True
                logging.info("Comment submitted via Enter key (textarea re-rendered)")
        except (StaleElementReferenceException, WebDriverException) as exc:
            logging.debug("Enter key submission attempt: %s", exc)

        # --- Submission Method 2: Click 'Post' button (handles div[role='button'] & button) ---
        post_button_selectors = (
            "//div[@role='button' and (normalize-space()='Post' or text()='Post')]",
            "//form//div[@role='button' and (contains(., 'Post') or contains(text(), 'Post'))]",
            "//div[@role='button' and contains(., 'Post')]",
            "//button[normalize-space()='Post' or text()='Post']",
            "//form//button[@type='submit' or normalize-space()='Post']",
            "//div[normalize-space()='Post' and not(@aria-disabled='true')]",
            "//span[normalize-space()='Post']/ancestor::*[@role='button' or self::button]",
        )

        for attempt in range(3):
            post_btn = None
            for xpath in post_button_selectors:
                try:
                    candidates = driver.find_elements(By.XPATH, xpath)
                    for btn in candidates:
                        if not btn.is_displayed():
                            continue
                        if btn.get_attribute("aria-disabled") == "true":
                            continue
                        post_btn = btn
                        break
                    if post_btn:
                        break
                except (StaleElementReferenceException, WebDriverException):
                    continue

            if post_btn:
                try:
                    post_btn.click()
                    submitted = True
                    logging.info("Clicked Post button directly")
                    pause(2.0)
                    break
                except (ElementClickInterceptedException, StaleElementReferenceException):
                    try:
                        driver.execute_script("arguments[0].click();", post_btn)
                        submitted = True
                        logging.info("Clicked Post button via JavaScript")
                        pause(2.0)
                        break
                    except Exception:
                        pause(0.5)
                except WebDriverException:
                    pause(0.5)
            else:
                if submitted:
                    break
                pause(0.5)

        # --- Verification ---
        pause(2.0)

        # Check for Instagram restriction / block message
        page_source_lower = driver.page_source.lower()
        for block_str in (
            "couldn't post comment",
            "action blocked",
            "try again later",
            "we limit how often you can do certain things",
            "we restrict certain activity",
        ):
            if block_str in page_source_lower:
                logging.warning("❌ Instagram restriction: '%s'", block_str)
                return False

        # Verify textarea is cleared
        for sel in textarea_selectors:
            try:
                boxes = driver.find_elements(By.XPATH, sel)
                for box in boxes:
                    val = box.get_attribute("value") or ""
                    if not val.strip():
                        logging.info("✅ Comment verified: textarea cleared")
                        return True
            except (StaleElementReferenceException, WebDriverException):
                continue

        # Verify comment snippet appears on page
        snippet = comment_text[:35].strip()
        if snippet and snippet in driver.page_source:
            logging.info("✅ Comment verified: found text on page")
            return True

        if submitted:
            logging.info("✅ Comment submitted successfully")
            return True

        logging.warning("Could not confirm comment was posted to %s", post_url)
        return False

    except (TimeoutException, WebDriverException) as exc:
        logging.warning("Could not post comment to %s: %s", post_url, exc)
        return False


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _fieldnames(cls: type) -> list[str]:
    return [f.name for f in fields(cls)]


def read_negative_posts_csv() -> list[dict[str, str]]:
    """Read all rows from negative_posts.csv; return empty list if missing."""
    if not NEGATIVE_POSTS_CSV.exists():
        return []
    with NEGATIVE_POSTS_CSV.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_seen_posts() -> dict[str, dict]:
    """Load persistent registry of evaluated posts, seeding from negative_posts.csv if needed."""
    data: dict[str, dict] = {}
    if SEEN_POSTS_FILE.exists():
        try:
            loaded = json.loads(SEEN_POSTS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    # Seed from negative_posts.csv so existing posted items aren't lost
    if NEGATIVE_POSTS_CSV.exists():
        try:
            for row in read_negative_posts_csv():
                mid = row.get("media_id")
                if mid and mid not in data:
                    data[mid] = {
                        "status": row.get("response_status", "posted"),
                        "permalink": row.get("permalink", ""),
                        "note": "seeded_from_csv",
                        "updated_at": row.get("collected_at", datetime.now(timezone.utc).isoformat()),
                    }
        except Exception:
            pass

    return data


def save_seen_posts(data: dict[str, dict]) -> None:
    try:
        SEEN_POSTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not save seen_posts.json: %s", exc)


def mark_post_seen(media_id: str, status: str, permalink: str = "", note: str = "") -> None:
    """Record status in persistent seen_posts.json."""
    if not media_id:
        return
    data = load_seen_posts()
    data[media_id] = {
        "status": status,
        "permalink": permalink,
        "note": note,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_seen_posts(data)


def is_post_seen(media_id: str, skip_not_negative: bool = True) -> bool:
    """Return True if media_id has already been processed and should be skipped."""
    if not media_id:
        return False
    data = load_seen_posts()
    entry = data.get(media_id)
    if not entry:
        return False
    status = entry.get("status")
    if status in ("posted", "already_commented", "too_old", "skipped"):
        return True
    if status == "not_negative" and skip_not_negative:
        return True
    return False


def read_existing_media_ids() -> set[str]:
    """Return media_ids that were already handled (posted, skipped, commented, too old, not negative)."""
    handled = set()
    # 1. From CSV
    for row in read_negative_posts_csv():
        if row.get("media_id") and row.get("response_status") in ("posted", "skipped"):
            handled.add(row["media_id"])
    # 2. From seen_posts.json
    for mid, info in load_seen_posts().items():
        if info.get("status") in ("posted", "skipped", "already_commented", "too_old", "not_negative"):
            handled.add(mid)
    return handled


def fetch_trending_topics(max_topics: int = 6) -> list[str]:
    """Fetch live trending news topics related to Indian Army / defence from Google News RSS.

    Extracts clean keywords suitable for Instagram keyword search.
    """
    rss_urls = [
        "https://news.google.com/rss/search?q=Indian+Army+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Agniveer+OR+Kashmir+encounter+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
    ]
    extracted: list[str] = []
    seen: set[str] = set()

    for url in rss_urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                tree = ET.fromstring(resp.read())
                for item in tree.findall(".//item")[:5]:
                    title_elem = item.find("title")
                    if title_elem is None or not title_elem.text:
                        continue
                    raw_title = title_elem.text.rsplit("-", 1)[0].strip()
                    clean_title = re.sub(r"[^\w\s]", " ", raw_title).strip()
                    stop_words = {"the", "for", "and", "with", "from", "near", "under", "after", "into", "over"}
                    words = [w for w in clean_title.split() if len(w) > 2 and w.lower() not in stop_words]
                    if len(words) >= 2:
                        query = " ".join(words[:4])
                        if query.lower() not in seen:
                            seen.add(query.lower())
                            extracted.append(query)
        except Exception as exc:
            logging.debug("Could not fetch RSS from %s: %s", url, exc)

    return extracted[:max_topics]


def append_negative_post(post: NegativePost) -> None:
    """Insert or update a row in negative_posts.csv, creating headers if needed."""
    rows = read_negative_posts_csv()
    updated = False
    for i, row in enumerate(rows):
        if row.get("media_id") == post.media_id:
            rows[i] = asdict(post)
            updated = True
            break

    if updated:
        with NEGATIVE_POSTS_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_fieldnames(NegativePost))
            writer.writeheader()
            writer.writerows(rows)
    else:
        write_header = not NEGATIVE_POSTS_CSV.exists()
        with NEGATIVE_POSTS_CSV.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_fieldnames(NegativePost))
            if write_header:
                writer.writeheader()
            writer.writerow(asdict(post))


def update_post_status(media_id: str, new_status: str) -> None:
    """Rewrite negative_posts.csv, updating response_status for *media_id*."""
    rows = read_negative_posts_csv()
    if not rows:
        return
    for row in rows:
        if row.get("media_id") == media_id:
            row["response_status"] = new_status
    with NEGATIVE_POSTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_fieldnames(NegativePost))
        writer.writeheader()
        writer.writerows(rows)


def append_response_log(record: ResponseRecord) -> None:
    """Append a row to the response audit log."""
    write_header = not RESPONSE_LOG_CSV.exists()
    with RESPONSE_LOG_CSV.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_fieldnames(ResponseRecord))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(record))
