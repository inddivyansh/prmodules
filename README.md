# Indian Army PR Bot 🇮🇳

An automated, intelligent Instagram monitoring and public relations tool. It monitors hashtags and search keywords for negative or misleading posts concerning the Indian Army, evaluates content sentiment using a local Ollama LLM, generates respectful and patriotic counter-responses in the language of the post, and automatically comments in real-time.

---

## Key Features

- **Single-Pass Real-Time Workflow (`bot.py`)**:
  Scans, detects negative sentiment, generates a contextual counter-response, and posts a comment immediately on the spot before moving to the next post.
- **Ultra-Strict Sentiment Analysis**:
  Evaluates post captions using a local LLM (Ollama). Even a slight degree of negativity (or presence of anti-army hashtags such as `#indianarmycrimes`, `#armyatrocities`) triggers counter-engagement.
- **Language-Adaptive Counter-Responses**:
  Dynamically replies in the language and dialect of the source post (Hindi, Urdu, English, Hinglish, etc.) while upholding a dignified, professional, and patriotic tone.
- **3-Step Human-Like Login System**:
  1. **Session Cookie Restore**: Loads `.instagram_cookies.json` to bypass login entirely if a valid session exists.
  2. **Humanized Typing**: Types username and password character-by-character with randomized human keystroke intervals (0.05s–0.18s).
  3. **2FA / OTP Challenge Terminal Wait**: Detects two-factor/SMS/email authentication challenges, pauses execution, displays a countdown in your terminal, and lets you enter the code directly in the browser.
  4. **Interactive Fallback**: Allows manual login in the browser window if Instagram serves unexpected security challenges.
- **Robust Anti-Detection Engine**:
  Configured to look like a standard user browser—disables `navigator.webdriver` via CDP, removes automation flags (`--disable-blink-features=AutomationControlled`), uses standard desktop Chrome headers, and automatically dismisses cookie banners and "Save Info" dialogs.
- **Resilient React Comment Submission**:
  Engineered specifically for Instagram's dynamic React DOM:
  - Avoids redundant page reloads if already viewing the post.
  - Dynamically re-queries elements to eliminate `StaleElementReferenceException`.
  - Submits comments via keyboard `Enter` (`Keys.RETURN`) and multi-selector "Post" button clicks (`div[@role='button']`, `form//div`, `button[@type='submit']`) with JavaScript fallback.
  - Verifies comment appearance and detects Instagram restriction banners.
- **Full Audit Trail**:
  Maintains detailed logs in `negative_posts.csv`, `response_log.csv`, and `monitor.log`. Allows retrying failed attempts without duplicate entries.

---

## Workflow Overview

```
[Start bot.py]
      │
      ▼
[3-Step Login: Cookies ➔ Human Typing ➔ OTP Wait ➔ Manual Fallback]
      │
      ▼
[Scan Source Feeds (Negative Hashtags, Keywords, Official Feeds)]
      │
      ▼
[Extract Post Caption & Check Duplicate History]
      │
      ▼
[Ollama Sentiment Check: Is post negative toward Indian Army?]
      │
      ├── (NO) ──► Skip & proceed to next post
      │
      └── (YES) ──► Generate Contextual Counter-Response
                          │
                          ▼
                    [Post Comment via Resilient Selenium Engine]
                          │
                          ▼
                    [Log Status to negative_posts.csv & response_log.csv]
                          │
                          ▼
                    [Paced Human Delay (e.g. 90s) before next action]
```

---

## Prerequisites

1. **Python 3.10+**: Ensure Python is installed and added to your `PATH`.
2. **Google Chrome**: Modern desktop Google Chrome installed.
3. **Ollama**: Running locally with your chosen model.
   ```powershell
   # Install model (run once)
   ollama pull llama3.2
   ```

---

## Quick Start

### 1. Setup Virtual Environment & Install Dependencies

```powershell
# Create virtual environment (if not already created)
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install required packages
pip install -r requirements.txt
```

### 2. Configure Environment

Copy the template and edit `.env`:

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in your Instagram credentials and customize settings:

```env
# Instagram Credentials
INSTAGRAM_USERNAME=your_username
INSTAGRAM_PASSWORD=your_password

# Hashtags to monitor (comma-separated, without #)
POSITIVE_HASHTAGS=indianarmy,indianarmedforces,southerncommand,adgpi,jaihind
NEGATIVE_HASHTAGS=indianarmycrimes,armyatrocities,kashmirviolence,humanrightsviolation

# Search keywords to monitor (comma-separated)
SEARCH_KEYWORDS=indian army fake,army brutality,military torture,false encounter

# Local Ollama model
OLLAMA_MODEL=llama3.2

# Bot Parameters
MAX_POSTS_TO_SCAN_PER_SOURCE=20
MAX_COMMENTS_PER_SESSION=6
DELAY_BETWEEN_COMMENTS_SECONDS=90
MAX_COMMENT_LENGTH=220
COMMENT_PREFIX=
DETECTION_STRATEGY=negative_first
HEADLESS=false
```

### 3. Ensure Ollama Is Running

Make sure Ollama is active on your system:
```powershell
ollama list
```

### 4. Run the Bot

```powershell
python bot.py
```
*Or using the virtual environment directly:*
```powershell
.\.venv\Scripts\python.exe bot.py
```

---

## Configuration Reference

| Environment Variable | Default | Description |
|---|---|---|
| `INSTAGRAM_USERNAME` | *(Required)* | Instagram login username / email. |
| `INSTAGRAM_PASSWORD` | *(Required)* | Instagram login password. |
| `NEGATIVE_HASHTAGS` | `indianarmycrimes,...` | Anti-army hashtags where all content is critically evaluated. |
| `POSITIVE_HASHTAGS` | `indianarmy,...` | Official / general military hashtags monitored for trolls or hostile comments. |
| `SEARCH_KEYWORDS` | `indian army fake,...` | Keywords searched in Instagram explore. |
| `OLLAMA_MODEL` | `llama3.2` | Ollama model used for detection and response generation. |
| `DETECTION_STRATEGY` | `negative_first` | `negative_first`: Negative tags first, then keywords, then positive tags.<br>`balanced`: Alternates between sources.<br>`positive_only`: Only scans positive tags for brigading. |
| `MAX_POSTS_TO_SCAN_PER_SOURCE` | `20` | Maximum posts collected per hashtag/keyword. |
| `MAX_COMMENTS_PER_SESSION` | `6` | Safety cap on total comments posted during a single run. |
| `DELAY_BETWEEN_COMMENTS_SECONDS` | `90` | Base wait time between successive comments (randomized ±30%). |
| `MAX_COMMENT_LENGTH` | `220` | Maximum character limit for generated responses. |
| `COMMENT_PREFIX` | *(Empty)* | Optional text prefixed to every comment (e.g., `[Official Response]`). |
| `HEADLESS` | `false` | `false`: Shows Chrome window (recommended for monitoring / OTP).<br>`true`: Runs in background. |

---

## Output Files & Audit Logs

| File | Purpose |
|---|---|
| `negative_posts.csv` | Record of all flagged posts: ID, username, permalink, caption snippet, sentiment, and response status (`posted`, `failed`, `skipped`). |
| `response_log.csv` | Full audit log containing the exact generated response text, post link, timestamp, and status. |
| `monitor.log` | Complete timestamped console and execution log for debugging. |
| `.instagram_cookies.json` | Persisted session cookies used for subsequent automatic logins. |

---

## Viewing Results in PowerShell

Monitor logs in real-time:
```powershell
Get-Content monitor.log -Tail 25 -Wait
```

View all flagged posts:
```powershell
Import-Csv negative_posts.csv | Format-Table -Property media_id, username, source_tag, response_status
```

View posted comments:
```powershell
Import-Csv response_log.csv | Where-Object { $_.status -eq "posted" } | Format-Table -Property permalink, generated_response, responded_at
```

---

## Project Structure

```
PR/
├── bot.py                  # All-in-one execution script (scan, detect, reply, comment)
├── shared.py               # Core automation library (Selenium driver, login, commenting, CSV I/O)
├── .env                    # Active configuration & credentials (gitignored)
├── .env.example            # Sample configuration template
├── requirements.txt        # Python package dependencies
├── negative_posts.csv      # Log of flagged posts (auto-generated)
├── response_log.csv        # Detailed comment audit history (auto-generated)
├── monitor.log             # Runtime log file (auto-generated)
└── .instagram_cookies.json # Saved session cookies (auto-generated)
```

---

## Safety & Best Practices

1. **Dedicated Account**: Use a dedicated account created for public relations activities.
2. **Moderate Volume**: Maintain a conservative session comment cap (`MAX_COMMENTS_PER_SESSION=4` to `6`) and realistic delays (`90` to `180` seconds) to avoid platform rate limits.
3. **Session Re-use**: Do not delete `.instagram_cookies.json` unnecessarily; re-using valid cookies reduces login requests and prevents verification challenges.
4. **First Run in Visible Mode**: Always run with `HEADLESS=false` initially so you can solve any 2FA or security prompts if presented by Instagram.
