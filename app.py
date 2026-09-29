"""Indian Army PR Command Center.

Autonomous counter-narrative monitoring, bot engine management,
real-time telemetry, human triage, and strategic situation reporting.
"""
from __future__ import annotations

import csv
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import streamlit as st

from clusterer import cluster_posts
from pipeline import analyze_instagram_url, analyze_post_content, publish_approved_comment
from reporter import generate_sitrep
from verifier import load_knowledge_base
from shared import (
    ACCOUNTS_FILE,
    ALT_COOKIES_FILE,
    COOKIES_FILE,
    NEGATIVE_POSTS_CSV,
    RESPONSE_LOG_CSV,
    ROOT,
    fetch_trending_topics,
    get_cookies_file,
    load_accounts,
    load_seen_posts,
    read_negative_posts_csv,
)
from vpn_manager import detect_installed_vpn, get_current_ip, get_ip_details, rotate_vpn
from account_tracker import get_account_record, get_all_account_telemetry, is_account_cooling

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Indian Army PR Command Center",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Professional dark console styling (zero emojis, defense-grade theme)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
.main {
    background-color: #090d16;
    color: #e2e8f0;
}
section[data-testid="stSidebar"] {
    background-color: #0d131f;
    border-right: 1px solid #1e293b;
}

/* Metric styling */
[data-testid="stMetric"] {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 8px;
    padding: 12px 16px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
}
[data-testid="stMetricLabel"] {
    color: #94a3b8 !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
[data-testid="stMetricValue"] {
    color: #f8fafc !important;
    font-size: 1.45rem !important;
    font-weight: 700 !important;
    font-family: 'JetBrains Mono', monospace;
}

/* Buttons */
.stButton > button[kind="primary"] {
    background: #2563eb;
    border: 1px solid #1d4ed8;
    border-radius: 6px;
    color: #ffffff;
    font-weight: 600;
    letter-spacing: 0.02em;
    padding: 8px 16px;
    transition: all 0.15s ease-in-out;
}
.stButton > button[kind="primary"]:hover {
    background: #1d4ed8;
    border-color: #1e40af;
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.35);
}
.stButton > button[kind="secondary"] {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #cbd5e1;
    font-weight: 500;
    transition: all 0.15s ease-in-out;
}
.stButton > button[kind="secondary"]:hover {
    background: #334155;
    color: #ffffff;
}

/* Status pills */
.status-pill-running {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    background: rgba(16, 185, 129, 0.12);
    color: #10b981;
    border: 1px solid rgba(16, 185, 129, 0.35);
}
.status-pill-idle {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    background: rgba(100, 116, 139, 0.12);
    color: #94a3b8;
    border: 1px solid rgba(100, 116, 139, 0.3);
}

.notice-box-ok {
    background: rgba(16, 185, 129, 0.08);
    border: 1px solid rgba(16, 185, 129, 0.25);
    border-radius: 6px;
    padding: 8px 12px;
    color: #10b981;
    font-size: 0.8rem;
    font-weight: 500;
    margin: 6px 0;
}
.notice-box-warn {
    background: rgba(245, 158, 11, 0.08);
    border: 1px solid rgba(245, 158, 11, 0.25);
    border-radius: 6px;
    padding: 8px 12px;
    color: #f59e0b;
    font-size: 0.8rem;
    font-weight: 500;
    margin: 6px 0;
}

/* Terminal Console Feed */
.log-feed {
    background: #06090e;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 12px 14px;
    height: 320px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 0.78rem;
    line-height: 1.5;
    color: #cbd5e1;
    display: flex;
    flex-direction: column-reverse;
}
.log-line-default { color: #94a3b8; }
.log-line-info { color: #38bdf8; }
.log-line-success { color: #34d399; font-weight: 500; }
.log-line-warn { color: #fbbf24; }
.log-line-error { color: #f87171; font-weight: 600; }

/* Comment feed card */
.comment-card {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 6px;
    padding: 12px 14px;
    margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# File paths and configuration defaults
# ---------------------------------------------------------------------------
CONFIG_PATH = ROOT / "bot_config.json"
ENV_PATH = ROOT / ".env"

DEFAULT_CONFIG = {
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
    "account_cooldown_minutes": 15,
    "neg_hashtags": "indianarmycrimes,armyatrocities,kashmirviolence,humanrightsviolation",
    "pos_hashtags": "indianarmy,indianarmedforces,adgpi,jaihind",
    "keywords": "indian army viral,kashmir encounter,agniveer protest,indian army fake",
    "dry_run": False,
}

STRATEGY_OPTIONS = ["trending_first", "negative_first", "balanced", "positive_only"]
STRATEGY_DESCRIPTIONS = {
    "trending_first": "Live military news queries evaluated first, followed by keywords and hashtags.",
    "negative_first": "Negative hashtag feeds prioritized to intercept anti-army narratives immediately.",
    "balanced": "All sources shuffled evenly across the monitoring session.",
    "positive_only": "Official hashtag feeds monitored specifically for coordinated troll brigading.",
}
MODEL_OPTIONS = ["llama3.2", "llama3.2:1b", "qwen2.5:3b", "qwen2.5:1.5b", "mistral"]


def _load_env_credentials() -> tuple[str, str]:
    """Read Instagram credentials from .env."""
    user = os.getenv("INSTAGRAM_USERNAME", "")
    pwd = os.getenv("INSTAGRAM_PASSWORD", "")
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("INSTAGRAM_USERNAME="):
                    user = line.split("=", 1)[1].strip()
                elif line.startswith("INSTAGRAM_PASSWORD="):
                    pwd = line.split("=", 1)[1].strip()
        except Exception:
            pass
    return user, pwd


def _load_persisted_config() -> dict:
    """Read saved operational config from bot_config.json if available."""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    return cfg


# Initialize session state once
if "initialized" not in st.session_state:
    env_u, env_p = _load_env_credentials()
    saved_cfg = _load_persisted_config()
    st.session_state.ig_username = env_u
    st.session_state.ig_password = env_p
    st.session_state.ollama_model = saved_cfg.get("model", "llama3.2")
    st.session_state.strategy = saved_cfg.get("strategy", "trending_first")
    st.session_state.max_per_source = int(saved_cfg.get("max_per_source", 20))
    st.session_state.max_comments = int(saved_cfg.get("max_comments", 12))
    st.session_state.delay_seconds = int(saved_cfg.get("delay_seconds", 120))
    st.session_state.max_age_days = int(saved_cfg.get("max_age_days", 14))
    st.session_state.headless = bool(saved_cfg.get("headless", False))
    st.session_state.comment_prefix = str(saved_cfg.get("comment_prefix", ""))
    st.session_state.enable_trending = bool(saved_cfg.get("enable_trending", True))
    st.session_state.shuffle_sources = bool(saved_cfg.get("shuffle_sources", True))
    st.session_state.enable_vpn_rotation = bool(saved_cfg.get("enable_vpn_rotation", False))
    st.session_state.vpn_rotate_command = str(saved_cfg.get("vpn_rotate_command", ""))
    st.session_state.vpn_cooldown_seconds = int(saved_cfg.get("vpn_cooldown_seconds", 8))
    st.session_state.comments_per_account = int(saved_cfg.get("comments_per_account", 3))
    st.session_state.account_cooldown_minutes = int(saved_cfg.get("account_cooldown_minutes", 15))
    st.session_state.neg_hashtags = str(saved_cfg.get("neg_hashtags", DEFAULT_CONFIG["neg_hashtags"]))
    st.session_state.pos_hashtags = str(saved_cfg.get("pos_hashtags", DEFAULT_CONFIG["pos_hashtags"]))
    st.session_state.keywords = str(saved_cfg.get("keywords", DEFAULT_CONFIG["keywords"]))
    st.session_state.dry_run = bool(saved_cfg.get("dry_run", False))
    st.session_state.bot_process = None
    st.session_state.bot_running = False
    st.session_state.processed_posts = []
    st.session_state.clusters = []
    st.session_state.trending_topics = []
    st.session_state.raw_input = ""
    st.session_state.initialized = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _save_credentials(user: str, pwd: str) -> None:
    """Write credentials strictly to .env."""
    lines = [
        "# Instagram Credentials (only sensitive data lives here)",
        f"INSTAGRAM_USERNAME={user.strip()}",
        f"INSTAGRAM_PASSWORD={pwd.strip()}",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _save_config_file(cfg: dict) -> None:
    """Write operational settings to bot_config.json."""
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def _cookie_status() -> tuple[bool, str]:
    """Check Instagram cookie file status."""
    target_file = None
    if COOKIES_FILE.exists():
        target_file = COOKIES_FILE
    elif ALT_COOKIES_FILE.exists():
        target_file = ALT_COOKIES_FILE

    if target_file:
        try:
            d = json.loads(target_file.read_text(encoding="utf-8"))
            if isinstance(d, list) and d:
                return True, f"Active session cookies detected ({len(d)} cookies) — auto-login enabled"
        except Exception:
            pass
    return False, "No saved session cookies — browser login required on first run"


# Module-level singleton process handle across Streamlit reruns
if "_BOT_PROCESS_SINGLETON" not in globals():
    _BOT_PROCESS_SINGLETON = None


def _get_python_executable() -> str:
    """Find the best Python executable with required dependencies."""
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    venv_py_posix = ROOT / ".venv" / "bin" / "python"
    if venv_py_posix.exists():
        return str(venv_py_posix)
    return sys.executable


def _bot_alive() -> bool:
    """Check if the background bot process is currently running."""
    global _BOT_PROCESS_SINGLETON
    p = st.session_state.get("bot_process") or _BOT_PROCESS_SINGLETON
    if p is None:
        return False
    if p.poll() is not None:
        _BOT_PROCESS_SINGLETON = None
        st.session_state.bot_running = False
        st.session_state.bot_process = None
        return False
    _BOT_PROCESS_SINGLETON = p
    st.session_state.bot_running = True
    st.session_state.bot_process = p
    return True


def _ollama_status() -> tuple[bool, str]:
    """Check if the local Ollama server is responding."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434", timeout=1.5) as resp:
            if resp.status == 200:
                return True, "Ollama LLM Engine: Online"
    except Exception:
        pass
    return False, "Ollama LLM Engine: Offline"


def _ensure_ollama_server() -> bool:
    """Ensure Ollama server is active in background."""
    ok, _ = _ollama_status()
    if ok:
        return True
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _launch_bot() -> None:
    """Spawn the bot process in the background using the project virtualenv."""
    global _BOT_PROCESS_SINGLETON
    _ensure_ollama_server()
    py_bin = _get_python_executable()
    log_path = ROOT / "monitor.log"
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} INFO Launching bot engine ({py_bin})\n")
    log_f.flush()

    p = subprocess.Popen(
        [py_bin, str(ROOT / "bot.py")],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    _BOT_PROCESS_SINGLETON = p
    st.session_state.bot_process = p
    st.session_state.bot_running = True


def _stop_bot() -> None:
    """Terminate the active bot process."""
    global _BOT_PROCESS_SINGLETON
    p = st.session_state.get("bot_process") or _BOT_PROCESS_SINGLETON
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    _BOT_PROCESS_SINGLETON = None
    st.session_state.bot_running = False
    st.session_state.bot_process = None



def _tail_log(n: int = 80) -> list[str]:
    """Read the latest n lines from monitor.log."""
    f = ROOT / "monitor.log"
    if not f.exists():
        return []
    try:
        return open(f, encoding="utf-8", errors="replace").readlines()[-n:]
    except Exception:
        return []


def _log_html(line: str) -> str:
    """Color-code a single log line cleanly without emojis."""
    line = line.strip()
    if not line:
        return ""
    lo = line.lower()
    cls = "log-line-default"
    if "[posted]" in lo or "comment verified" in lo or "posted" in lo:
        cls = "log-line-success"
    elif "error" in lo or "crash" in lo or "failed" in lo:
        cls = "log-line-error"
    elif "warning" in lo or "skip" in lo or "warn" in lo or "waiting" in lo:
        cls = "log-line-warn"
    elif "[negative]" in lo or "sentiment" in lo or "scanning source" in lo:
        cls = "log-line-info"

    safe = html.escape(line)
    return f'<div class="{cls}">{safe}</div>'


def _stats() -> dict:
    """Calculate actual cumulative statistics from real tracking files."""
    rows = read_negative_posts_csv()
    seen = load_seen_posts()
    return {
        "flagged": len(rows),
        "posted": sum(1 for r in rows if r.get("response_status") == "posted"),
        "failed": sum(1 for r in rows if r.get("response_status") == "failed"),
        "skipped": sum(1 for r in rows if r.get("response_status") == "skipped"),
        "not_neg": sum(1 for v in seen.values() if v.get("status") == "not_negative"),
        "too_old": sum(1 for v in seen.values() if v.get("status") == "too_old"),
        "total_seen": len(seen),
    }


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### PR COMMAND CENTER")
    st.caption("Indian Army Counter-Narrative Operations")
    st.markdown("---")

    alive = _bot_alive()
    if alive:
        pid = st.session_state.bot_process.pid if st.session_state.bot_process else "ACTIVE"
        st.markdown(f'<span class="status-pill-running">SESSION RUNNING &bull; PID {pid}</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="status-pill-idle">SYSTEM STANDBY &bull; IDLE</span>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("**SESSION TELEMETRY**")
    stats_data = _stats()
    st.metric("Comments Posted", stats_data["posted"])
    st.metric("Posts Flagged", stats_data["flagged"])
    st.metric("Total Posts Scanned", stats_data["total_seen"])

    st.markdown("---")
    st.markdown("**AUTHENTICATION**")
    cookie_ok, cookie_msg = _cookie_status()
    notice_class = "notice-box-ok" if cookie_ok else "notice-box-warn"
    st.markdown(f'<div class="{notice_class}">{cookie_msg}</div>', unsafe_allow_html=True)

    if cookie_ok:
        if st.button("Clear Saved Cookies", width='stretch', type="secondary"):
            try:
                COOKIES_FILE.unlink()
                st.success("Session cookies removed.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    st.markdown("---")
    st.markdown("**LLM ENGINE**")
    ollama_ok, ollama_msg = _ollama_status()
    o_badge = "notice-box-ok" if ollama_ok else "notice-box-warn"
    st.markdown(f'<div class="{o_badge}">{ollama_msg}</div>', unsafe_allow_html=True)
    if not ollama_ok:
        if st.button("Start Ollama Service", width='stretch', key="btn_sidebar_ollama"):
            _ensure_ollama_server()
            st.success("Ollama service started.")
            st.rerun()

    st.markdown("---")
    st.caption(f"Log: monitor.log ({'Active' if (ROOT / 'monitor.log').exists() else 'Empty'})")
    st.caption(f"Registry: negative_posts.csv ({stats_data['flagged']} entries)")


# ---------------------------------------------------------------------------
# Main header
# ---------------------------------------------------------------------------
st.markdown("## Indian Army &mdash; PR Command Center")
st.caption("Autonomous Sentiment Monitoring &bull; RAG Fact-Check Verification &bull; Counter-Narrative Publishing &bull; Situation Reporting")

tab_bot, tab_triage, tab_kb, tab_sitrep = st.tabs([
    "Bot Operations",
    "Manual Triage",
    "Knowledge Base",
    "Situation Report",
])


# ===========================================================================
# TAB 1: BOT OPERATIONS (ALL-IN-ONE FLOW)
# ===========================================================================
with tab_bot:
    st.subheader("Bot Operations & Engine Configuration")
    st.caption("All operational settings, target sources, controls, live activity, and performance metrics in one consolidated view.")

    # -----------------------------------------------------------------------
    # ALL BOT SETTINGS IN ONE UNIFIED SECTION
    # -----------------------------------------------------------------------
    with st.container(border=True):
        st.markdown("#### Operational Configuration")

        # 1. Credentials
        st.markdown("**Account Authentication**")
        st.caption("Credentials are saved directly to .env. Once logged in, session cookies persist automatically.")
        c1, c2 = st.columns(2)
        with c1:
            ui_user = st.text_input(
                "Instagram Username / Handle",
                value=st.session_state.ig_username,
                key="cfg_username",
            )
        with c2:
            ui_pass = st.text_input(
                "Instagram Password",
                value=st.session_state.ig_password,
                type="password",
                key="cfg_password",
            )

        cookie_active, cookie_text = _cookie_status()
        c_badge = "notice-box-ok" if cookie_active else "notice-box-warn"
        st.markdown(f'<div class="{c_badge}">{cookie_text}</div>', unsafe_allow_html=True)

        # Multi-Account & VPN Rotation Section
        with st.expander("Multi-Account Rotation & Automated VPN Switching", expanded=False):
            st.caption("Rotate between multiple Instagram accounts with isolated browser profiles and dynamic VPN network switching.")

            # Network egress telemetry
            net_info = get_ip_details(timeout=2.0)
            st.markdown(
                f"""<div style="background:#0d131f; border:1px solid #1e293b; border-radius:6px; padding:10px 14px; margin-bottom:12px;">
                    <div style="color:#94a3b8; font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">Current Public Network Egress</div>
                    <div style="display:flex; align-items:baseline; gap:8px; margin-top:2px;">
                        <span style="font-family:'JetBrains Mono',monospace; font-size:1.1rem; font-weight:600; color:#38bdf8;">{net_info.get('ip', 'UNKNOWN')}</span>
                        <span style="color:#94a3b8; font-size:0.85rem;">&bull; {net_info.get('summary', '')}</span>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )

            raw_accounts = ACCOUNTS_FILE.read_text(encoding="utf-8") if ACCOUNTS_FILE.exists() else ""
            ui_accounts = st.text_area(
                "Multi-Account Credentials (accounts.txt)",
                value=raw_accounts,
                height=90,
                placeholder="# Format: username:password (one per line)\narmy_supporter_01:pass123\narmy_supporter_02:pass456",
                help="Format: username:password (one per line). When populated, the bot rotates through these accounts automatically.",
                key="cfg_accounts_txt",
            )

            # Count accounts
            parsed_accts = [line.strip().split(":", 1) for line in ui_accounts.splitlines() if line.strip() and not line.strip().startswith("#") and ":" in line]
            if parsed_accts:
                st.info(f"Loaded {len(parsed_accts)} rotation account(s): {', '.join(f'@{u.strip()}' for u, _ in parsed_accts)}")

            v_col1, v_col2 = st.columns(2)
            with v_col1:
                ui_enable_vpn = st.checkbox(
                    "Enable Automated VPN Rotation",
                    value=st.session_state.enable_vpn_rotation,
                    key="cfg_enable_vpn",
                    help="Trigger dynamic VPN rotation command before switching to each account.",
                )
                ui_cpa = st.number_input(
                    "Comments per Account before rotating",
                    min_value=1,
                    max_value=20,
                    value=int(st.session_state.comments_per_account),
                    step=1,
                    key="cfg_comments_per_acct",
                )
            with v_col2:
                ui_acct_cooldown = st.number_input(
                    "Account Safety Cooldown (minutes)",
                    min_value=0,
                    max_value=180,
                    value=int(st.session_state.account_cooldown_minutes),
                    step=5,
                    key="cfg_acct_cooldown",
                    help="Resting period before an account can be selected again by the rotation engine to avoid spam triggers.",
                )
                ui_vpn_cd = st.number_input(
                    "Network Settle Timeout (seconds)",
                    min_value=3,
                    max_value=60,
                    value=int(st.session_state.vpn_cooldown_seconds),
                    step=1,
                    key="cfg_vpn_cd",
                    help="Maximum seconds to poll for dynamic IP change after triggering VPN switch.",
                )

            ui_vpn_cmd = st.text_input(
                "VPN Rotation Command (or script)",
                value=st.session_state.vpn_rotate_command,
                placeholder="e.g. python vpn_manager.py rotate",
                key="cfg_vpn_cmd",
                help="CLI command or script executed prior to launching each account. Leave blank to auto-detect installed CLIs (Windscribe, Proton, WARP) or rotate_vpn.bat.",
            )

            t_col1, t_col2 = st.columns([1, 2])
            with t_col1:
                if st.button("Test VPN Rotation Now", key="btn_test_vpn", width='stretch'):
                    with st.spinner("Executing dynamic VPN rotation test..."):
                        res = rotate_vpn(command=ui_vpn_cmd, cooldown_seconds=int(ui_vpn_cd))
                        if res.get("changed"):
                            st.success(f"IP changed: {res.get('old_ip')} -> {res.get('new_ip')}")
                        else:
                            st.info(f"Public IP: {res.get('new_ip')} ({res.get('message', '')})")
            with t_col2:
                detected_vpns = detect_installed_vpn()
                if detected_vpns:
                    st.caption("Detected CLI tools: " + ", ".join(f"`{k}`" for k in detected_vpns.keys()))
                else:
                    st.caption("Custom script template available in `rotate_vpn.bat`.")

            # Account Telemetry and Health Table
            all_accounts_to_show = [u.strip() for u, _ in parsed_accts]
            if ui_user.strip() and ui_user.strip() not in all_accounts_to_show:
                all_accounts_to_show.insert(0, ui_user.strip())

            if all_accounts_to_show:
                st.markdown("---")
                st.markdown("**Account Registry & Health Telemetry**")
                table_rows = []
                for acct in all_accounts_to_show:
                    rec = get_account_record(acct)
                    is_cool, rem = is_account_cooling(acct, cooldown_minutes=int(ui_acct_cooldown))
                    p_c, a_c = get_cookies_file(acct)
                    cookie_state = "Active" if (p_c.exists() or a_c.exists()) else "None"

                    if is_cool:
                        status_text = f"Cooling ({rem // 60}m {rem % 60}s)"
                    else:
                        status_text = rec.get("status", "ready").capitalize()

                    prof_dir = ROOT / "profiles" / acct.lower()
                    prof_state = "Isolated" if prof_dir.exists() else "Ready on Launch"

                    table_rows.append({
                        "Account": f"@{acct}",
                        "Cookies": cookie_state,
                        "Profile": prof_state,
                        "Status": status_text,
                        "Last IP": rec.get("last_ip", "UNKNOWN"),
                        "Location": rec.get("last_location", "") or "—",
                        "Posted": rec.get("total_comments_posted", 0),
                    })
                st.dataframe(table_rows, width='stretch', hide_index=True)

        st.markdown("---")

        # 2. Target Sources
        st.markdown("**Target Monitoring Sources**")
        st.caption("Configure anti-army hashtags, official feeds, and keyword queries to monitor on Instagram.")

        ui_neg = st.text_area(
            "Negative Hashtags (comma-separated, without #)",
            value=st.session_state.neg_hashtags,
            height=75,
            key="cfg_neg",
            help="High-risk feeds monitored specifically for anti-army narratives. Every post is evaluated by the LLM.",
        )

        ui_pos = st.text_area(
            "Positive / Official Hashtags (comma-separated, without #)",
            value=st.session_state.pos_hashtags,
            height=65,
            key="cfg_pos",
            help="Official army feeds monitored for adversarial troll brigading.",
        )

        ui_kw = st.text_area(
            "Search Keywords (comma-separated)",
            value=st.session_state.keywords,
            height=75,
            key="cfg_kw",
            help="Keyword queries scanned across Instagram Explore search.",
        )

        # Real Trending News Integration
        st.markdown("**Live Defense News Aggregation**")
        t_col1, t_col2 = st.columns([1, 3])
        with t_col1:
            if st.button("Fetch Trending Defense News", width='stretch', key="btn_fetch_trends"):
                with st.spinner("Fetching latest defense headlines from RSS..."):
                    st.session_state.trending_topics = fetch_trending_topics(max_topics=6)

        with t_col2:
            if st.session_state.trending_topics:
                st.write("Current trending military topics: " + ", ".join(f"`{t}`" for t in st.session_state.trending_topics))
                if st.button("Append Topics to Search Keywords", key="btn_merge_trends"):
                    current_kws = [k.strip() for k in ui_kw.split(",") if k.strip()]
                    merged = list(dict.fromkeys(current_kws + st.session_state.trending_topics))
                    st.session_state.keywords = ", ".join(merged)
                    st.rerun()

        st.markdown("---")

        # 3. Operational Parameters
        st.markdown("**Session Parameters & Rate Limits**")

        p_col1, p_col2 = st.columns(2)
        with p_col1:
            strat_idx = STRATEGY_OPTIONS.index(st.session_state.strategy) if st.session_state.strategy in STRATEGY_OPTIONS else 0
            ui_strategy = st.selectbox("Detection Strategy", STRATEGY_OPTIONS, index=strat_idx, key="cfg_strategy")
            st.caption(STRATEGY_DESCRIPTIONS.get(ui_strategy, ""))

            model_idx = MODEL_OPTIONS.index(st.session_state.ollama_model) if st.session_state.ollama_model in MODEL_OPTIONS else 0
            ui_model_select = st.selectbox("Ollama Model", MODEL_OPTIONS, index=model_idx, key="cfg_model")
            ui_model_custom = st.text_input("Custom Model Override (optional)", placeholder="e.g. gemma2:9b", key="cfg_custom_model")
            effective_model = ui_model_custom.strip() if ui_model_custom.strip() else ui_model_select

        with p_col2:
            ui_max_comments = st.slider(
                "Max Comments per Session",
                min_value=1,
                max_value=30,
                value=st.session_state.max_comments,
                help="Recommended <= 20 to prevent Instagram rate-limiting.",
                key="cfg_max_comments",
            )
            ui_delay = st.slider(
                "Comment Delay (seconds)",
                min_value=60,
                max_value=600,
                value=st.session_state.delay_seconds,
                step=10,
                help="Recommended 90-180 seconds to simulate natural interaction pacing.",
                key="cfg_delay",
            )
            ui_max_age = st.slider(
                "Max Post Age (days)",
                min_value=1,
                max_value=60,
                value=st.session_state.max_age_days,
                help="Skip posts published earlier than this cutoff.",
                key="cfg_max_age",
            )
            ui_per_source = st.slider(
                "Max Posts to Scan per Source",
                min_value=5,
                max_value=50,
                value=st.session_state.max_per_source,
                key="cfg_per_source",
            )

        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        with f_col1:
            ui_headless = st.checkbox(
                "Headless Browser (Hidden)",
                value=st.session_state.headless,
                key="cfg_headless",
                help="Leave unchecked to show the Chrome browser on your desktop so you can complete Instagram human verification / CAPTCHA / 2FA.",
            )
        with f_col2:
            ui_trending = st.checkbox("Enable Trending News", value=st.session_state.enable_trending, key="cfg_trending")
        with f_col3:
            ui_shuffle = st.checkbox("Shuffle Sources", value=st.session_state.shuffle_sources, key="cfg_shuffle")
        with f_col4:
            ui_dry_run = st.checkbox("Simulation Mode (Dry-Run)", value=st.session_state.dry_run, key="cfg_dry_run")

        ui_prefix = st.text_input(
            "Comment Prefix (optional)",
            value=st.session_state.comment_prefix,
            placeholder="e.g. [Indian Army Fact-Check Response]",
            key="cfg_prefix",
        )

    # -----------------------------------------------------------------------
    # ACTION CONTROL BAR: LAUNCH BOT, SAVE CONFIG, REFRESH
    # -----------------------------------------------------------------------
    def _sync_and_save() -> dict:
        """Sync form inputs to session state and persist to disk."""
        st.session_state.ig_username = ui_user.strip()
        st.session_state.ig_password = ui_pass.strip()
        st.session_state.neg_hashtags = ui_neg.strip()
        st.session_state.pos_hashtags = ui_pos.strip()
        st.session_state.keywords = ui_kw.strip()
        st.session_state.strategy = ui_strategy
        st.session_state.ollama_model = effective_model
        st.session_state.max_comments = ui_max_comments
        st.session_state.delay_seconds = ui_delay
        st.session_state.max_age_days = ui_max_age
        st.session_state.max_per_source = ui_per_source
        st.session_state.headless = ui_headless
        st.session_state.enable_trending = ui_trending
        st.session_state.shuffle_sources = ui_shuffle
        st.session_state.dry_run = ui_dry_run
        st.session_state.comment_prefix = ui_prefix.strip()
        st.session_state.enable_vpn_rotation = ui_enable_vpn
        st.session_state.vpn_rotate_command = ui_vpn_cmd.strip()
        st.session_state.vpn_cooldown_seconds = int(ui_vpn_cd)
        st.session_state.comments_per_account = int(ui_cpa)
        st.session_state.account_cooldown_minutes = int(ui_acct_cooldown)

        _save_credentials(st.session_state.ig_username, st.session_state.ig_password)

        if ui_accounts.strip():
            ACCOUNTS_FILE.write_text(ui_accounts.strip() + "\n", encoding="utf-8")
        elif ACCOUNTS_FILE.exists() and not ui_accounts.strip():
            try:
                ACCOUNTS_FILE.unlink()
            except Exception:
                pass

        cfg_dict = {
            "model": st.session_state.ollama_model,
            "strategy": st.session_state.strategy,
            "max_per_source": st.session_state.max_per_source,
            "max_comments": st.session_state.max_comments,
            "delay_seconds": st.session_state.delay_seconds,
            "max_comment_length": 220,
            "max_age_days": st.session_state.max_age_days,
            "headless": st.session_state.headless,
            "comment_prefix": st.session_state.comment_prefix,
            "enable_trending": st.session_state.enable_trending,
            "shuffle_sources": st.session_state.shuffle_sources,
            "enable_vpn_rotation": st.session_state.enable_vpn_rotation,
            "vpn_rotate_command": st.session_state.vpn_rotate_command,
            "vpn_cooldown_seconds": st.session_state.vpn_cooldown_seconds,
            "comments_per_account": st.session_state.comments_per_account,
            "account_cooldown_minutes": st.session_state.account_cooldown_minutes,
            "neg_hashtags": st.session_state.neg_hashtags,
            "pos_hashtags": st.session_state.pos_hashtags,
            "keywords": st.session_state.keywords,
            "dry_run": st.session_state.dry_run,
        }
        _save_config_file(cfg_dict)
        return cfg_dict

    st.markdown("---")
    btn_col1, btn_col2, btn_col3 = st.columns([2, 1, 1])

    with btn_col1:
        if not _bot_alive():
            if st.button("Launch Bot Session", type="primary", width='stretch'):
                has_auth = (ui_user.strip() and ui_pass.strip()) or (ACCOUNTS_FILE.exists() and bool(load_accounts(ACCOUNTS_FILE))) or bool(parsed_accts)
                if not has_auth:
                    st.error("Instagram credentials are required. Fill username/password or add accounts to accounts.txt.")
                else:
                    _sync_and_save()
                    _launch_bot()
                    st.success(f"Bot session initiated (PID: {st.session_state.bot_process.pid}).")
                    if ui_dry_run:
                        st.info("Simulation mode active: responses will be generated but not posted.")
                    st.rerun()
        else:
            if st.button("Stop Bot Session", type="secondary", width='stretch'):
                _stop_bot()
                st.warning("Bot session terminated by operator.")
                st.rerun()

    with btn_col2:
        if st.button("Save Configuration", width='stretch'):
            _sync_and_save()
            st.success("Configuration successfully saved to bot_config.json.")

    with btn_col3:
        if st.button("Refresh", width='stretch'):
            st.rerun()

    # -----------------------------------------------------------------------
    # LIVE ACTIVITY FEED & TELEMETRY (AUTO-UPDATING FRAGMENT EVERY 2 SECONDS)
    # -----------------------------------------------------------------------
    @st.fragment(run_every=2)
    def _render_live_activity_and_telemetry() -> None:
        st.markdown("---")
        f_h1, f_h2 = st.columns([3, 1])
        with f_h1:
            st.markdown("#### Live Activity Feed")
        with f_h2:
            alive = _bot_alive()
            if alive:
                st.markdown('<span class="status-pill-running">LIVE STREAMING &bull; 2S</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="status-pill-idle">SYSTEM STANDBY</span>', unsafe_allow_html=True)

        active = _bot_alive()
        if active:
            st.markdown(f'<span class="status-pill-running">SESSION ACTIVE &bull; PID {st.session_state.bot_process.pid}</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="status-pill-idle">SYSTEM STANDBY</span>', unsafe_allow_html=True)

        log_lines = _tail_log(80)
        if log_lines:
            feed_html = "".join(_log_html(l) for l in reversed(log_lines))
        else:
            feed_html = '<div class="log-line-default">No activity recorded yet. Launch a session to begin streaming output.</div>'

        st.markdown(f'<div class="log-feed">{feed_html}</div>', unsafe_allow_html=True)

        feed_ctrl1, feed_ctrl2 = st.columns([4, 1])
        with feed_ctrl1:
            if active:
                st.caption("Active monitoring cycle running in background. Live output streams automatically every 2 seconds.")
            else:
                st.caption("Engine standby. Live streaming updates automatically once a session is launched.")
        with feed_ctrl2:
            if (ROOT / "monitor.log").exists():
                if st.button("Clear Log", width='stretch', key="btn_frag_clear_log"):
                    try:
                        (ROOT / "monitor.log").write_text("", encoding="utf-8")
                        st.success("Log cleared.")
                        st.rerun(scope="fragment")
                    except Exception as ex:
                        st.error(str(ex))

        # Performance Telemetry & Activity Records
        st.markdown("---")
        st.markdown("#### Performance Telemetry & Activity Records")
        st.caption("Real-time metrics and response logs generated from active monitoring (auto-updating live).")

        current_stats = _stats()
        sc = st.columns(6)
        sc[0].metric("Comments Posted", current_stats["posted"])
        sc[1].metric("Posts Flagged", current_stats["flagged"])
        sc[2].metric("Posts Skipped", current_stats["skipped"])
        sc[3].metric("Filtered (Not Neg)", current_stats["not_neg"])
        sc[4].metric("Aged Out", current_stats["too_old"])
        sc[5].metric("Total Scanned", current_stats["total_seen"])

        # Recent Comments Section
        st.markdown("##### Recent Published Comments")
        posted_records = []
        if RESPONSE_LOG_CSV.exists():
            try:
                with open(RESPONSE_LOG_CSV, newline="", encoding="utf-8") as f:
                    all_records = list(csv.DictReader(f))
                    posted_records = [r for r in all_records if r.get("status") == "posted"]
            except Exception:
                posted_records = []

        if posted_records:
            for r in reversed(posted_records[-6:]):
                with st.container(border=True):
                    rc1, rc2 = st.columns([5, 1])
                    with rc1:
                        ts = r.get("responded_at", "")[:19].replace("T", " ")
                        permalink = r.get("permalink", "")
                        media_id = r.get("media_id", "Post")
                        if permalink:
                            st.markdown(f"**Target:** [{media_id}]({permalink}) &bull; *Timestamp: {ts} UTC*")
                        else:
                            st.markdown(f"**Target:** {media_id} &bull; *Timestamp: {ts} UTC*")

                        caption_snip = r.get("caption_snippet", "").strip()
                        if caption_snip:
                            st.caption(f'Original post excerpt: "{caption_snip}"')

                        response_text = r.get("generated_response", "").strip()
                        st.write(f"**Deployed Counter-Response:** {response_text}")

                    with rc2:
                        st.markdown('<span class="status-pill-running">POSTED</span>', unsafe_allow_html=True)
        else:
            st.info("No counter-comments published yet. Published comments will appear here automatically.")

        # Flagged Posts Data Table
        if NEGATIVE_POSTS_CSV.exists():
            with st.expander("View Full Negative Posts Register", expanded=False):
                try:
                    with open(NEGATIVE_POSTS_CSV, newline="", encoding="utf-8") as f:
                        neg_rows = list(csv.DictReader(f))
                    if neg_rows:
                        st.dataframe(neg_rows, width='stretch')
                    else:
                        st.write("No flagged posts registered.")
                except Exception as e:
                    st.error(f"Error reading register: {e}")

    _render_live_activity_and_telemetry()


# ===========================================================================
# TAB 2: MANUAL TRIAGE & PUBLISHING
# ===========================================================================
with tab_triage:
    st.subheader("Manual Target Triage & Fact-Check Publishing")
    st.caption("Perform deep content analysis on specific Instagram URLs or captions, verify against official sources, and approve counter-responses.")

    input_text = st.text_area(
        "Target Instagram URLs or Captions (one per line). Format: URL :: Caption or standalone URL",
        value=st.session_state.get("raw_input", ""),
        height=110,
        key="triage_input",
    )

    t_btn1, t_btn2 = st.columns([2, 1])
    with t_btn1:
        run_batch = st.button("Run Deep Content Analysis", type="primary", width='stretch')
    with t_btn2:
        if st.button("Clear Review Queue", width='stretch'):
            st.session_state.processed_posts = []
            st.session_state.clusters = []
            st.session_state.raw_input = ""
            st.rerun()

    if run_batch:
        lines = [l.strip() for l in input_text.splitlines() if l.strip()]
        if not lines:
            st.error("Please enter at least one URL or caption to analyze.")
        else:
            results = []
            prog = st.progress(0, text="Initializing analysis engine...")
            model_to_use = st.session_state.ollama_model
            for i, line in enumerate(lines):
                prog.progress((i + 1) / len(lines), text=f"Analyzing item {i + 1} of {len(lines)}...")
                try:
                    if "::" in line:
                        url, cap = [p.strip() for p in line.split("::", 1)]
                        media_id = url.split("/")[-2] if "/p/" in url else f"item_{i}"
                        res = analyze_post_content({
                            "url": url,
                            "username": "monitored",
                            "caption": cap,
                            "media_id": media_id,
                            "image_url": "",
                            "ocr_text": "",
                        }, model=model_to_use)
                    elif line.startswith("http"):
                        res = analyze_instagram_url(line, model=model_to_use, headless=True)
                    else:
                        res = analyze_post_content({
                            "url": "",
                            "username": "manual_input",
                            "caption": line,
                            "media_id": f"text_{i}",
                            "image_url": "",
                            "ocr_text": "",
                        }, model=model_to_use)
                    res["item_id"] = i
                    results.append(res)
                except Exception as exc:
                    st.warning(f"Item {i + 1} analysis warning: {exc}")

            prog.empty()
            if results:
                clustering = cluster_posts(results)
                st.session_state.processed_posts = clustering.get("posts", results)
                st.session_state.clusters = clustering.get("clusters", [])
            st.success(f"Analysis complete: {len(results)} items evaluated.")
            st.rerun()

    reviewed = st.session_state.processed_posts
    if not reviewed:
        st.info("Review queue empty. Submit target URLs or captions above to start triage.")
    else:
        st.markdown(f"---")
        st.markdown(f"#### Review Queue ({len(reviewed)} items)")
        for i, item in enumerate(reviewed):
            post = item.get("post", {})
            ana = item.get("analysis", {})
            verif = item.get("verification", [])
            status = item.get("approval_status", "PENDING")
            sentiment = ana.get("sentiment", "NEUTRAL")

            with st.container(border=True):
                h1, h2 = st.columns([5, 1])
                with h1:
                    post_url = post.get("url", "")
                    if post_url:
                        st.markdown(f"**Item #{i+1}** &bull; [{post_url}]({post_url}) &bull; Cluster: *{item.get('cluster_label', 'General')}*")
                    else:
                        st.markdown(f"**Item #{i+1}** &bull; Text Analysis &bull; Cluster: *{item.get('cluster_label', 'General')}*")
                with h2:
                    if status == "APPROVED":
                        st.markdown('<span class="status-pill-running">APPROVED</span>', unsafe_allow_html=True)
                    elif status == "REJECTED":
                        st.markdown('<span class="status-pill-idle">REJECTED</span>', unsafe_allow_html=True)
                    else:
                        st.markdown('<span class="notice-box-warn">PENDING</span>', unsafe_allow_html=True)

                m_cols = st.columns(4)
                m_cols[0].metric("Category", ana.get("category", "General"))
                m_cols[1].metric("Sentiment", sentiment)
                m_cols[2].metric("Language", ana.get("language", "English"))
                m_cols[3].metric("Identified Claims", len(ana.get("claims", [])))

                st.text_area("Caption Content", post.get("caption", ""), height=65, disabled=True, key=f"triage_cap_{i}")

                for v in verif:
                    v_status = v.get("status", "UNVERIFIED")
                    st.markdown(f"&bull; **Claim:** {v.get('claim', '')} &mdash; `[{v_status}]`")
                    if v.get("source_name"):
                        st.caption(f"Verification Source: {v['source_name']}")

                draft_text = st.text_area(
                    "Generated Counter-Response Draft:",
                    value=item.get("response", ""),
                    height=80,
                    key=f"triage_draft_{i}",
                    disabled=(status == "APPROVED"),
                )
                item["response"] = draft_text

                act1, act2, act3 = st.columns([2, 1, 3])
                with act1:
                    if st.button("Approve & Publish", key=f"btn_app_{i}", type="primary", disabled=(status == "APPROVED")):
                        with st.spinner("Publishing counter-comment..."):
                            ok, msg = publish_approved_comment(
                                post_url=post.get("url", ""),
                                comment_text=draft_text,
                                dry_run=st.session_state.dry_run,
                            )
                        if ok:
                            item["approval_status"] = "APPROVED"
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                with act2:
                    if st.button("Reject", key=f"btn_rej_{i}", disabled=(status == "REJECTED")):
                        item["approval_status"] = "REJECTED"
                        st.rerun()
                with act3:
                    if status != "PENDING" and st.button("Reset Status", key=f"btn_rst_{i}"):
                        item["approval_status"] = "PENDING"
                        st.rerun()


# ===========================================================================
# TAB 3: KNOWLEDGE BASE
# ===========================================================================
with tab_kb:
    st.subheader("Official Fact-Check & Verification Knowledge Base")
    st.caption("Official press clarifications, PIB fact-checks, and MoD statements used for RAG-grounded counter-narratives.")

    kb_entries = load_knowledge_base()
    search_q = st.text_input("Filter Knowledge Base", "", placeholder="Enter keyword, claim, or source topic...")

    filtered_kb = (
        [
            e for e in kb_entries
            if search_q.lower() in e["topic"].lower()
            or search_q.lower() in e["claim_pattern"].lower()
            or any(search_q.lower() in k.lower() for k in e.get("keywords", []))
        ]
        if search_q
        else kb_entries
    )

    for entry in filtered_kb:
        with st.container(border=True):
            kb_c1, kb_c2 = st.columns([4, 1])
            with kb_c1:
                st.markdown(f"#### [{entry['id']}] {entry['topic']}")
                st.write(f"**Addressed Claim:** {entry['claim_pattern']}")
                st.markdown(f"**Official Verification:**\n> {entry['evidence']}")
                st.caption(f"Authority: {entry['source_name']} &bull; Date: {entry.get('date', 'N/A')}")
            with kb_c2:
                ver_status = entry.get("official_status", "VERIFIED")
                st.markdown(f"`[{ver_status}]`")
                if entry.get("source_url"):
                    st.link_button("View Official Notice", entry["source_url"])


# ===========================================================================
# TAB 4: SITUATION REPORT
# ===========================================================================
with tab_sitrep:
    st.subheader("Automated Operational Situation Report (SitRep)")
    st.caption("Strategic executive intelligence briefing compiled from the current monitoring and triage cycle.")

    triage_posts = st.session_state.processed_posts
    cluster_data = st.session_state.clusters

    if not triage_posts:
        st.info("No items in current session review queue. Run monitoring or triage to generate a SitRep.")
    else:
        sitrep_doc = generate_sitrep(processed_posts=triage_posts, clusters=cluster_data)
        time_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        st.download_button(
            "Download Situation Report (.md)",
            data=sitrep_doc,
            file_name=f"SitRep_IndianArmy_{time_slug}.md",
            mime="text/markdown",
            type="primary",
        )
        st.markdown("---")
        st.markdown(sitrep_doc)
