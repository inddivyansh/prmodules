"""Indian Army PR Command Center — Full Dashboard.

All bot configuration, session launching, live monitoring, triage,
analytics and reporting in one Streamlit dashboard.  No terminal needed.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import streamlit as st

from clusterer import cluster_posts
from pipeline import analyze_instagram_url, analyze_post_content, publish_approved_comment
from reporter import generate_sitrep
from verifier import load_knowledge_base
from shared import (
    COOKIES_FILE,
    NEGATIVE_POSTS_CSV,
    RESPONSE_LOG_CSV,
    ROOT,
    fetch_trending_topics,
    load_seen_posts,
    read_negative_posts_csv,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Indian Army PR Command Center",
    page_icon="🇮🇳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;}
.main{background:#0a0c10;}
section[data-testid="stSidebar"]{background:#0d1117;border-right:1px solid #21262d;}
[data-testid="stMetric"]{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:12px 16px;}
[data-testid="stMetricLabel"]{color:#8b949e!important;font-size:.75rem;font-weight:500;}
[data-testid="stMetricValue"]{color:#f0f6fc!important;font-size:1.5rem;font-weight:700;}
.stButton>button[kind="primary"]{
  background:linear-gradient(135deg,#1f6feb,#388bfd);border:none;border-radius:8px;
  color:#fff;font-weight:600;transition:all .2s;box-shadow:0 0 12px rgba(31,111,235,.4);}
.stButton>button[kind="primary"]:hover{transform:translateY(-1px);box-shadow:0 4px 20px rgba(31,111,235,.6);}
.session-live{display:inline-flex;align-items:center;gap:6px;
  background:rgba(35,134,54,.15);border:1px solid #238636;border-radius:20px;
  padding:4px 14px;color:#3fb950;font-size:.82rem;font-weight:700;animation:pulse 2s infinite;}
.session-idle{display:inline-flex;align-items:center;gap:6px;
  background:rgba(139,148,158,.1);border:1px solid #30363d;border-radius:20px;
  padding:4px 14px;color:#8b949e;font-size:.82rem;font-weight:600;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
.cookie-ok{background:rgba(35,134,54,.1);border:1px solid #238636;border-radius:8px;
  padding:8px 12px;color:#3fb950;font-size:.85rem;margin:4px 0;}
.cookie-miss{background:rgba(187,128,9,.1);border:1px solid #9e6a03;border-radius:8px;
  padding:8px 12px;color:#d29922;font-size:.85rem;margin:4px 0;}
.log-feed{background:#0d1117;border:1px solid #21262d;border-radius:8px;
  padding:14px;height:300px;overflow-y:auto;
  font-family:'Courier New',monospace;font-size:.77rem;color:#e6edf3;}
.lo{color:#3fb950;}.li{color:#79c0ff;}.lw{color:#d29922;}.le{color:#f85149;}
</style>
""", unsafe_allow_html=True)

# ── Session-state defaults ─────────────────────────────────────────────────────
_D: dict = {
    "bot_process": None, "bot_running": False,
    "processed_posts": [], "clusters": [], "trending_topics": [], "raw_input": "",
    "ig_username": "", "ig_password": "",
    "ollama_model": "llama3.2",
    "neg_hashtags": "indianarmycrimes,armyatrocities,kashmirviolence,humanrightsviolation",
    "pos_hashtags": "indianarmy,indianarmedforces,adgpi,jaihind",
    "keywords": "indian army viral,kashmir encounter,agniveer protest,indian army fake",
    "strategy": "trending_first",
    "max_comments": 12, "delay_seconds": 120, "max_age_days": 14, "max_per_source": 20,
    "headless": False, "enable_trending": True, "shuffle_sources": True,
    "comment_prefix": "", "dry_run": False,
}
for _k, _v in _D.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Load .env once into session state ─────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _env_snap() -> dict:
    try:
        from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    except Exception: pass
    def _i(k, d): 
        try: return int(os.getenv(k, str(d)))
        except: return d
    return {
        "ig_username":     os.getenv("INSTAGRAM_USERNAME", ""),
        "ig_password":     os.getenv("INSTAGRAM_PASSWORD", ""),
        "ollama_model":    os.getenv("OLLAMA_MODEL", "llama3.2"),
        "neg_hashtags":    os.getenv("NEGATIVE_HASHTAGS", "indianarmycrimes,armyatrocities,kashmirviolence,humanrightsviolation"),
        "pos_hashtags":    os.getenv("POSITIVE_HASHTAGS", "indianarmy,indianarmedforces,adgpi,jaihind"),
        "keywords":        os.getenv("SEARCH_KEYWORDS", "indian army viral,kashmir encounter,agniveer protest"),
        "strategy":        os.getenv("DETECTION_STRATEGY", "trending_first"),
        "max_comments":    _i("MAX_COMMENTS_PER_SESSION", 12),
        "delay_seconds":   _i("DELAY_BETWEEN_COMMENTS_SECONDS", 120),
        "max_age_days":    _i("MAX_POST_AGE_DAYS", 14),
        "max_per_source":  _i("MAX_POSTS_TO_SCAN_PER_SOURCE", 20),
        "headless":        os.getenv("HEADLESS", "false").lower() == "true",
        "enable_trending": os.getenv("ENABLE_TRENDING_NEWS", "true").lower() == "true",
        "shuffle_sources": os.getenv("SHUFFLE_SOURCES", "true").lower() == "true",
        "comment_prefix":  os.getenv("COMMENT_PREFIX", ""),
    }

for _k, _v in _env_snap().items():
    if not st.session_state.get(_k) and _v:
        st.session_state[_k] = _v

# ── Helper functions ──────────────────────────────────────────────────────────
def _save_env() -> None:
    lines = [
        "# Instagram Credentials",
        f"INSTAGRAM_USERNAME={st.session_state.ig_username}",
        f"INSTAGRAM_PASSWORD={st.session_state.ig_password}",
        "",
        "# Hashtag Sources",
        f"NEGATIVE_HASHTAGS={st.session_state.neg_hashtags}",
        f"POSITIVE_HASHTAGS={st.session_state.pos_hashtags}",
        "",
        "# Keyword Sources",
        f"SEARCH_KEYWORDS={st.session_state.keywords}",
        "",
        "# AI Model",
        f"OLLAMA_MODEL={st.session_state.ollama_model}",
        "",
        "# Bot Behaviour",
        f"DETECTION_STRATEGY={st.session_state.strategy}",
        f"MAX_POSTS_TO_SCAN_PER_SOURCE={st.session_state.max_per_source}",
        f"MAX_COMMENTS_PER_SESSION={st.session_state.max_comments}",
        f"DELAY_BETWEEN_COMMENTS_SECONDS={st.session_state.delay_seconds}",
        "MAX_COMMENT_LENGTH=220",
        f"COMMENT_PREFIX={st.session_state.comment_prefix}",
        f"MAX_POST_AGE_DAYS={st.session_state.max_age_days}",
        f"HEADLESS={'true' if st.session_state.headless else 'false'}",
        f"ENABLE_TRENDING_NEWS={'true' if st.session_state.enable_trending else 'false'}",
        f"SHUFFLE_SOURCES={'true' if st.session_state.shuffle_sources else 'false'}",
    ]
    (ROOT / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cookie_status() -> tuple[bool, str]:
    if COOKIES_FILE.exists():
        try:
            d = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list) and d:
                return True, f"✅ {len(d)} session cookies — auto-login active"
        except Exception: pass
    return False, "⚠️ No saved cookies — browser login required on first run"


def _bot_alive() -> bool:
    p = st.session_state.get("bot_process")
    if p is None: return False
    if p.poll() is not None:
        st.session_state.bot_running = False
        st.session_state.bot_process = None
        return False
    return True


def _launch_bot() -> None:
    _save_env()
    p = subprocess.Popen(
        [sys.executable, str(ROOT / "bot.py")],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    st.session_state.bot_process = p
    st.session_state.bot_running = True


def _stop_bot() -> None:
    p = st.session_state.get("bot_process")
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill()
    st.session_state.bot_running = False
    st.session_state.bot_process = None


def _tail_log(n: int = 80) -> list[str]:
    f = ROOT / "monitor.log"
    if not f.exists(): return []
    try: return open(f, encoding="utf-8", errors="replace").readlines()[-n:]
    except: return []


def _log_html(line: str) -> str:
    line = line.strip()
    if not line: return ""
    cls = "li"
    lo = line.lower()
    if "✅" in line or ("posted" in lo and "not" not in lo): cls = "lo"
    elif "error" in lo or "crash" in lo or "❌" in line: cls = "le"
    elif "warning" in lo or "[skip]" in lo or "warn" in lo: cls = "lw"
    safe = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return f'<div class="{cls}">{safe}</div>'


def _stats() -> dict:
    rows = read_negative_posts_csv()
    seen = load_seen_posts()
    return {
        "flagged":    len(rows),
        "posted":     sum(1 for r in rows if r.get("response_status") == "posted"),
        "failed":     sum(1 for r in rows if r.get("response_status") == "failed"),
        "skipped":    sum(1 for r in rows if r.get("response_status") == "skipped"),
        "not_neg":    sum(1 for v in seen.values() if v.get("status") == "not_negative"),
        "too_old":    sum(1 for v in seen.values() if v.get("status") == "too_old"),
        "total_seen": len(seen),
    }


_STRATS = ["trending_first", "negative_first", "balanced", "positive_only"]
_STRAT_HELP = {
    "trending_first": "📡 Live news first — best for catching viral anti-army posts early (recommended)",
    "negative_first": "🔴 Anti-army hashtags prioritised — high precision, focused",
    "balanced":       "⚖️ All sources shuffled — wide coverage each session",
    "positive_only":  "🟢 Official feeds only — detects troll brigading",
}
_MODELS = ["llama3.2", "llama3.2:1b", "qwen2.5:3b", "qwen2.5:1.5b", "mistral"]

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🇮🇳 PR Command Center")
    st.caption("Indian Army Counter-Narrative Engine v2.0")
    st.markdown("---")

    alive = _bot_alive()
    badge = "session-live" if alive else "session-idle"
    label = "🟢 &nbsp;SESSION ACTIVE" if alive else "⚪ &nbsp;NO ACTIVE SESSION"
    st.markdown(f'<span class="{badge}">{label}</span>', unsafe_allow_html=True)

    st.markdown("---")
    s = _stats()
    st.metric("💬 Comments Posted",  s["posted"])
    st.metric("🚨 Posts Flagged",    s["flagged"])
    st.metric("🗂 Total Posts Seen", s["total_seen"])
    st.markdown("---")

    ok, msg = _cookie_status()
    css = "cookie-ok" if ok else "cookie-miss"
    st.markdown(f'<div class="{css}">{msg}</div>', unsafe_allow_html=True)
    if ok and st.button("🗑️ Clear Cookies", use_container_width=True):
        try: COOKIES_FILE.unlink(); st.success("Cookies cleared."); st.rerun()
        except Exception as e: st.error(str(e))

    st.markdown("---")
    st.caption("📄 Logs: `monitor.log`")
    st.caption("📊 Data: `negative_posts.csv`")

# ── Main header ───────────────────────────────────────────────────────────────
st.markdown("## 🇮🇳 Indian Army — PR Command Center")
st.caption("Counter-narrative automation · Sentiment monitoring · Human-in-the-loop publishing · Situation reporting")

tab_bot, tab_triage, tab_analytics, tab_kb, tab_sitrep = st.tabs([
    "🤖  Bot Control Room",
    "📋  Triage & Publishing",
    "📊  Analytics",
    "🛡️  Knowledge Base",
    "📑  SitRep",
])

# ═════════════════════════════════════════════════════════════════
# TAB 1 — BOT CONTROL ROOM
# ═════════════════════════════════════════════════════════════════
with tab_bot:

    # ─ Account ────────────────────────────────────────────────────
    with st.expander("🔐  Instagram Account & Session", expanded=not bool(st.session_state.ig_username)):
        st.caption("Credentials saved to `.env`. Cookies persist after first login — no re-login needed.")
        a1, a2 = st.columns(2)
        with a1:
            new_user = st.text_input("Username / Email", value=st.session_state.ig_username,
                                     placeholder="your_handle", key="ui_user")
        with a2:
            new_pass = st.text_input("Password", value=st.session_state.ig_password,
                                     type="password", placeholder="••••••••", key="ui_pass")
        if st.button("💾  Save Account to .env", type="primary"):
            st.session_state.ig_username = new_user.strip()
            st.session_state.ig_password = new_pass.strip()
            _save_env()
            st.success(f"✅ Saved credentials for @{st.session_state.ig_username}")
            st.rerun()
        ok, msg = _cookie_status()
        css = "cookie-ok" if ok else "cookie-miss"
        st.markdown(f'<div class="{css}">{msg}</div>', unsafe_allow_html=True)
        if ok:
            st.caption("🔁 Bot auto-logs in via cookies — no password re-entry until Instagram expires the session.")
        else:
            st.caption("🖥️ First run opens Chrome for login; cookies are saved automatically.")

    st.markdown("---")

    # ─ Sources ────────────────────────────────────────────────────
    st.markdown("### 📡  Source Configuration")
    sc1, sc2 = st.columns(2)
    with sc1:
        with st.container(border=True):
            st.markdown("#### 🏷️  Hashtag Sources")
            neg_tags_val = st.text_area(
                "🔴 Negative Hashtags (comma-sep, no #)",
                value=st.session_state.neg_hashtags, height=90, key="ui_neg",
                help="Anti-army feeds — every post LLM-checked.")
            pos_tags_val = st.text_area(
                "🟢 Positive / Official Hashtags",
                value=st.session_state.pos_hashtags, height=70, key="ui_pos",
                help="Official feeds — detects troll brigading.")

    with sc2:
        with st.container(border=True):
            st.markdown("#### 🔍  Keyword Search Sources")
            kw_val = st.text_area(
                "Active Keywords (comma-sep)",
                value=st.session_state.keywords, height=90, key="ui_kw",
                help="Instagram Explore search queries.")
            st.markdown("**📡 Live Trending Topics**")
            tc1, tc2 = st.columns([1, 2])
            with tc1:
                if st.button("🔥 Fetch Now", use_container_width=True, key="btn_fetch"):
                    with st.spinner("Querying news RSS…"):
                        st.session_state.trending_topics = fetch_trending_topics(max_topics=6)
            with tc2:
                for t in st.session_state.trending_topics[:3]:
                    st.caption(f"• `{t}`")
            if st.session_state.trending_topics:
                if st.button("➕ Add All Trending to Keywords", key="btn_trends"):
                    cur = [k.strip() for k in kw_val.split(",") if k.strip()]
                    merged = list(dict.fromkeys(cur + st.session_state.trending_topics))
                    st.session_state.keywords = ", ".join(merged)
                    st.rerun()

    st.markdown("---")

    # ─ Parameters ─────────────────────────────────────────────────
    st.markdown("### ⚙️  Session Parameters")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        _si = _STRATS.index(st.session_state.strategy) if st.session_state.strategy in _STRATS else 0
        strategy_val = st.selectbox("Detection Strategy", _STRATS, index=_si)
        st.caption(_STRAT_HELP.get(strategy_val, ""))
    with p2:
        _mi = _MODELS.index(st.session_state.ollama_model) if st.session_state.ollama_model in _MODELS else 0
        model_val = st.selectbox("Ollama Model", _MODELS, index=_mi)
        custom_m = st.text_input("Custom override", placeholder="e.g. gemma2:9b", key="ui_cm")
        final_model = custom_m.strip() if custom_m.strip() else model_val
    with p3:
        max_com_val = st.slider("Max Comments / Session", 1, 30, st.session_state.max_comments,
                                help="Keep ≤ 20 to stay under IG rate limits.")
        max_age_val = st.slider("Max Post Age (days)", 1, 60, st.session_state.max_age_days,
                                help="Skip posts older than this.")
    with p4:
        delay_val = st.slider("Comment Delay (seconds)", 60, 600, st.session_state.delay_seconds, step=10,
                              help="90–180 s mimics human pace.")
        per_src_val = st.slider("Max Posts per Source", 5, 50, st.session_state.max_per_source)

    av1, av2, av3, av4 = st.columns(4)
    with av1: headless_val = st.checkbox("Headless Chrome", st.session_state.headless)
    with av2: trending_val = st.checkbox("Live Trending News", st.session_state.enable_trending)
    with av3: shuffle_val  = st.checkbox("Shuffle Sources", st.session_state.shuffle_sources)
    with av4: dry_run_val  = st.checkbox("🔒 Dry-Run (no real posts)", st.session_state.dry_run)

    prefix_val = st.text_input("Comment Prefix (optional)",
                               value=st.session_state.comment_prefix,
                               placeholder="e.g. [Official Indian Army Response]")

    # ─ Launch Controls ─────────────────────────────────────────────
    st.markdown("---")

    def _sync() -> None:
        st.session_state.neg_hashtags    = neg_tags_val.strip()
        st.session_state.pos_hashtags    = pos_tags_val.strip()
        st.session_state.keywords        = kw_val.strip()
        st.session_state.strategy        = strategy_val
        st.session_state.ollama_model    = final_model
        st.session_state.max_comments    = max_com_val
        st.session_state.delay_seconds   = delay_val
        st.session_state.max_age_days    = max_age_val
        st.session_state.max_per_source  = per_src_val
        st.session_state.headless        = headless_val
        st.session_state.enable_trending = trending_val
        st.session_state.shuffle_sources = shuffle_val
        st.session_state.comment_prefix  = prefix_val
        st.session_state.dry_run         = dry_run_val

    lc1, lc2, lc3 = st.columns([2, 1, 1])
    with lc1:
        if not _bot_alive():
            if st.button("🚀  Launch Bot Session", type="primary", use_container_width=True):
                if not st.session_state.ig_username or not st.session_state.ig_password:
                    st.error("❌ Set Instagram credentials in the Account section above.")
                else:
                    _sync(); _launch_bot()
                    st.success(f"✅ Bot launched (PID {st.session_state.bot_process.pid}).")
                    if dry_run_val: st.info("🔒 Dry-run active — no real comments will be posted.")
                    st.rerun()
        else:
            if st.button("⏹️  Stop Bot Session", type="primary", use_container_width=True):
                _stop_bot(); st.warning("Bot session terminated."); st.rerun()
    with lc2:
        if st.button("💾  Save Config", use_container_width=True):
            _sync(); _save_env(); st.success("Saved to .env!")
    with lc3:
        if st.button("🔄  Refresh", use_container_width=True):
            st.rerun()

    # ─ Live Feed ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📟  Live Activity Feed")
    log_lines = _tail_log(80)
    log_html = "".join(_log_html(l) for l in log_lines) if log_lines \
        else '<div class="li">No log entries yet — launch a session to begin.</div>'
    st.markdown(f'<div class="log-feed">{log_html}</div>', unsafe_allow_html=True)
    if _bot_alive():
        st.caption("🔴 Session running — click 🔄 Refresh to update feed.")

    # ─ Stats ───────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📊  Cumulative Statistics")
    s = _stats()
    sc = st.columns(6)
    sc[0].metric("✅ Posted",    s["posted"])
    sc[1].metric("❌ Failed",    s["failed"])
    sc[2].metric("⏭ Skipped",   s["skipped"])
    sc[3].metric("🔕 Not Neg",  s["not_neg"])
    sc[4].metric("📅 Too Old",  s["too_old"])
    sc[5].metric("🗂 Seen",     s["total_seen"])

    # ─ Recent Comments ─────────────────────────────────────────────
    if RESPONSE_LOG_CSV.exists():
        st.markdown("---")
        st.markdown("#### 💬  Recent Comments Posted")
        try:
            rows = list(csv.DictReader(open(RESPONSE_LOG_CSV, newline="", encoding="utf-8")))
            for r in reversed([r for r in rows if r.get("status") == "posted"][-6:]):
                with st.container(border=True):
                    cc1, cc2 = st.columns([4, 1])
                    with cc1:
                        st.markdown(f"**[{r.get('responded_at','')[:16]}]** [{r.get('media_id','')}]({r.get('permalink','#')})")
                        st.caption((r.get("caption_snippet", "")[:120] + "…"))
                        st.info("💬 " + r.get("generated_response", ""))
                    with cc2:
                        st.success("POSTED")
        except Exception:
            pass

    # ─ Full Log Table ──────────────────────────────────────────────
    if NEGATIVE_POSTS_CSV.exists():
        with st.expander("📋  Full Negative Posts Log", expanded=False):
            try:
                rows = list(csv.DictReader(open(NEGATIVE_POSTS_CSV, newline="", encoding="utf-8")))
                st.dataframe(rows, use_container_width=True) if rows else st.info("No entries yet.")
            except Exception as e:
                st.error(str(e))


# ═════════════════════════════════════════════════════════════════
# TAB 2 — TRIAGE & PUBLISHING
# ═════════════════════════════════════════════════════════════════
with tab_triage:
    st.subheader("Human-in-the-Loop Triage & Publishing")
    st.caption("Deep-analyse URLs or captions, fact-check against official sources, then approve/reject responses.")

    with st.expander("🔥  Discover Trending Topics", expanded=False):
        tr1, tr2 = st.columns([1, 3])
        with tr1:
            if st.button("📡 Fetch", use_container_width=True, key="tr_fetch"):
                with st.spinner("Fetching…"):
                    st.session_state.trending_topics = fetch_trending_topics(max_topics=6)
        with tr2:
            if st.session_state.trending_topics:
                st.write(" • ".join(f"`{t}`" for t in st.session_state.trending_topics))
                if st.button("➕ Add as Search URLs", key="tr_add"):
                    from urllib.parse import quote as _q
                    urls = [f"https://www.instagram.com/explore/search/keyword/?q={_q(t)}"
                            for t in st.session_state.trending_topics]
                    st.session_state["raw_input"] = (
                        st.session_state.get("raw_input", "") + "\n" + "\n".join(urls)
                    ).strip()
                    st.rerun()

    input_text = st.text_area(
        "Instagram URLs or Captions (one per line). Use URL :: Caption for offline testing.",
        value=st.session_state.get("raw_input", ""), height=120, key="triage_ta",
    )
    tb1, tb2, tb3 = st.columns([2, 1, 2])
    with tb1:
        run_batch = st.button("🚀  Run Deep Analysis", type="primary", use_container_width=True)
    with tb2:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.processed_posts = []; st.session_state.clusters = []; st.rerun()
    with tb3:
        sp = ROOT / "test_captions.csv"
        if sp.exists() and st.button("📥 Load Benchmark Samples", use_container_width=True):
            with open(sp, encoding="utf-8") as f:
                r2 = list(csv.DictReader(f))
            st.session_state["raw_input"] = "\n".join(
                f"https://www.instagram.com/p/test_{r['id']}/ :: {r['caption']}" for r in r2[:6]
            )
            st.rerun()

    if run_batch:
        lines = [l.strip() for l in input_text.splitlines() if l.strip()]
        if not lines:
            st.error("Enter at least one URL or caption.")
        else:
            results = []
            prog = st.progress(0, text="Initialising…")
            _m = st.session_state.ollama_model
            for i, line in enumerate(lines):
                prog.progress((i+1)/len(lines), text=f"Analysing {i+1}/{len(lines)}…")
                try:
                    if "::" in line:
                        url, cap = [p.strip() for p in line.split("::", 1)]
                        res = analyze_post_content({
                            "url": url, "username": "monitored", "caption": cap,
                            "media_id": url.split("/")[-2] if "/p/" in url else f"p{i}",
                            "image_url": "", "ocr_text": "",
                        }, model=_m)
                    elif line.startswith("http"):
                        res = analyze_instagram_url(line, model=_m, headless=True)
                    else:
                        res = analyze_post_content({
                            "url": f"https://www.instagram.com/p/off_{i}/",
                            "username": "direct", "caption": line,
                            "media_id": f"off_{i}", "image_url": "", "ocr_text": "",
                        }, model=_m)
                    res["item_id"] = i; results.append(res)
                except Exception as exc:
                    st.warning(f"Item {i+1}: {exc}")
            prog.empty()
            if results:
                cl = cluster_posts(results)
                st.session_state.processed_posts = cl.get("posts", results)
                st.session_state.clusters = cl.get("clusters", [])
            st.success(f"✅ Processed {len(results)} items."); st.rerun()

    processed = st.session_state.processed_posts
    if not processed:
        st.info("Queue empty — paste URLs/captions above and click Run.")
    else:
        st.markdown(f"---\n### Review Queue — {len(processed)} item(s)")
        for i, item in enumerate(processed):
            post  = item.get("post", {})
            ana   = item.get("analysis", {})
            verif = item.get("verification", [])
            appr  = item.get("approval_status", "PENDING")
            sent  = ana.get("sentiment", "NEUTRAL")
            icon  = "🔴" if sent == "NEGATIVE" else ("🟢" if sent == "POSITIVE" else "⚪")
            with st.container(border=True):
                hh1, hh2 = st.columns([4, 1])
                with hh1:
                    st.markdown(f"**#{i+1}** [{post.get('url','?')}]({post.get('url','#')}) | "
                                f"@{post.get('username','?')} | *{item.get('cluster_label','—')}*")
                with hh2:
                    {"APPROVED": st.success, "REJECTED": st.error}.get(appr, st.warning)(appr)
                mc = st.columns(4)
                mc[0].metric("Category", ana.get("category","—"))
                mc[1].metric("Sentiment", f"{icon} {sent}")
                mc[2].metric("Language", ana.get("language","—"))
                mc[3].metric("Claims", len(ana.get("claims",[])))
                st.text_area("Caption", post.get("caption",""), height=65, disabled=True, key=f"cap_{i}")
                if post.get("ocr_text"): st.info("🖼️ " + post["ocr_text"])
                for v in verif:
                    vs = v.get("status","UNVERIFIED")
                    badge = ("❌ CONTRADICTED" if vs=="CONTRADICTED" else
                             "✅ SUPPORTED" if vs=="SUPPORTED" else "⚠️ UNVERIFIED")
                    st.markdown(f"- **{v.get('claim','')}** — {badge}")
                    if v.get("source_name"): st.caption("Source: "+v["source_name"])
                draft = st.text_area("Response Draft:", value=item.get("response",""),
                                     height=80, key=f"draft_{i}", disabled=(appr=="APPROVED"))
                item["response"] = draft
                bb1, bb2, bb3 = st.columns([2,1,3])
                with bb1:
                    if st.button("✅ Approve & Post", key=f"app_{i}", type="primary",
                                 disabled=(appr=="APPROVED")):
                        with st.spinner("Posting…"):
                            ok, msg = publish_approved_comment(
                                post_url=post.get("url",""), comment_text=draft,
                                dry_run=st.session_state.dry_run)
                        if ok:
                            item["approval_status"]="APPROVED"; st.success(msg); st.rerun()
                        else: st.error(msg)
                with bb2:
                    if st.button("❌ Reject", key=f"rej_{i}", disabled=(appr=="REJECTED")):
                        item["approval_status"]="REJECTED"; st.rerun()
                with bb3:
                    if appr!="PENDING" and st.button("↺ Reset", key=f"rst_{i}"):
                        item["approval_status"]="PENDING"; st.rerun()


# ═════════════════════════════════════════════════════════════════
# TAB 3 — ANALYTICS
# ═════════════════════════════════════════════════════════════════
with tab_analytics:
    st.subheader("Narrative & Trend Intelligence")
    s = _stats()
    ac = st.columns(4)
    ac[0].metric("Total Flagged",     s["flagged"])
    ac[1].metric("Comments Posted",   s["posted"],
                 delta=f"{s['posted']/max(1,s['flagged'])*100:.0f}% hit-rate")
    ac[2].metric("Cleared (Not Neg)", s["not_neg"])
    ac[3].metric("Too Old / Skipped", s["too_old"]+s["skipped"])

    if NEGATIVE_POSTS_CSV.exists():
        try:
            rows = list(csv.DictReader(open(NEGATIVE_POSTS_CSV, newline="", encoding="utf-8")))
            if rows:
                st.markdown("---"); st.subheader("Source Performance")
                src_map: dict = {}
                for r in rows:
                    tag = r.get("source_tag","unknown")
                    src_map.setdefault(tag, {"total":0,"posted":0})
                    src_map[tag]["total"] += 1
                    if r.get("response_status") == "posted": src_map[tag]["posted"] += 1
                for tag, d in sorted(src_map.items(), key=lambda x: -x[1]["total"]):
                    sa, sb, sc_ = st.columns([3,1,1])
                    sa.write(f"`{tag}`"); sb.write(f"Flagged: **{d['total']}**"); sc_.write(f"Posted: **{d['posted']}**")
        except Exception: pass

    clusters = st.session_state.clusters
    processed = st.session_state.processed_posts
    if clusters:
        st.markdown("---"); st.subheader("Narrative Clusters (Triage Session)")
        tp=len(processed)
        negs=sum(1 for p in processed if (p.get("analysis") or {}).get("sentiment")=="NEGATIVE")
        pos =sum(1 for p in processed if (p.get("analysis") or {}).get("sentiment")=="POSITIVE")
        tc=st.columns(4)
        tc[0].metric("Monitored",tp); tc[1].metric("Negative",negs)
        tc[2].metric("Positive",pos); tc[3].metric("Neutral",tp-negs-pos)
        for c in clusters:
            with st.container(border=True):
                ca,cb=st.columns([3,1])
                with ca:
                    st.markdown(f"#### 🏷️ {c['label']}")
                    st.write("Keywords: "+", ".join(f"`{k}`" for k in c.get("keywords",[])))
                with cb: st.metric("Posts",c["count"])
                st.caption("Sentiment: "+", ".join(f"{k}: {v}" for k,v in c.get("sentiment_breakdown",{}).items()))
    elif not processed:
        st.info("Run posts in the Triage tab to populate cluster analytics.")


# ═════════════════════════════════════════════════════════════════
# TAB 4 — KNOWLEDGE BASE
# ═════════════════════════════════════════════════════════════════
with tab_kb:
    st.subheader("Official Fact-Check & Verification Knowledge Base")
    st.caption("PIB fact-checks, MoD notices, and ADGPI clarifications used for RAG grounding.")
    kb = load_knowledge_base()
    q = st.text_input("🔍 Search:", "")
    filtered = (
        [e for e in kb if q.lower() in e["topic"].lower()
         or q.lower() in e["claim_pattern"].lower()
         or any(q.lower() in k.lower() for k in e.get("keywords",[]))]
        if q else kb
    )
    for e in filtered:
        with st.container(border=True):
            ke1,ke2=st.columns([3,1])
            with ke1:
                st.markdown(f"#### [{e['id']}] {e['topic']}")
                st.write(f"**Addressed Claim:** {e['claim_pattern']}")
                st.markdown(f"**Official Evidence:**\n> {e['evidence']}")
                st.caption(f"Authority: **{e['source_name']}** | Date: {e.get('date','N/A')}")
            with ke2:
                col="red" if e["official_status"]=="CONTRADICTED" else "green"
                st.markdown(f"**:{col}[{e['official_status']}]**")
                if e.get("source_url"): st.link_button("View Source",e["source_url"])


# ═════════════════════════════════════════════════════════════════
# TAB 5 — SITREP
# ═════════════════════════════════════════════════════════════════
with tab_sitrep:
    st.subheader("Automated Situation Report (SitRep)")
    st.caption("Executive briefing auto-generated from the current monitoring and triage cycle.")
    processed = st.session_state.processed_posts
    clusters  = st.session_state.clusters
    if not processed:
        st.info("No posts processed yet. Run the bot or use the Triage tab.")
    else:
        sitrep = generate_sitrep(processed_posts=processed, clusters=clusters)
        slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        st.download_button("📥  Download SitRep (.md)", data=sitrep,
                           file_name=f"SitRep_IndianArmy_{slug}.md",
                           mime="text/markdown", type="primary")
        st.markdown("---")
        st.markdown(sitrep)

