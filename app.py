from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st

from clusterer import cluster_posts
from pipeline import (
    analyze_instagram_url,
    analyze_post_content,
    publish_approved_comment,
)
from reporter import generate_sitrep
from verifier import load_knowledge_base
from shared import load_config

st.set_page_config(
    page_title="Indian Armed Forces PR Command Center",
    page_icon="🇮🇳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------
# Sidebar Configuration
# ---------------------------------------------------------
st.sidebar.title("🇮🇳 PR Command Center")
st.sidebar.caption("Open-Source Intelligence & Verification Engine")

model_options = [
    "llama3.2:latest",
    "llama3.2:1b",
    "qwen2.5:1.5b",
    "qwen2.5:3b",
]

selected_model = st.sidebar.selectbox(
    "Local SLM Model (8GB M3 Optimized)",
    options=model_options,
    index=0,
    help="Ultra-lightweight Small Language Models (1B-3B) tailored for Apple Silicon 8GB Unified RAM.",
)
custom_model = st.sidebar.text_input("Custom Model (optional override):", value="", placeholder="e.g. mistral:7b")
model = custom_model.strip() if custom_model.strip() else selected_model

st.sidebar.caption("💡 **8GB RAM Profile:** 1B–3B models consume only ~1.2–2.0 GB RAM with context capped at 2048 tokens, leaving 6+ GB for macOS.")

dry_run = st.sidebar.checkbox(
    "Dry-Run Mode (Safe Simulation)",
    value=True,
    help="When enabled, tests comments and verification without actual Instagram network submission.",
)

config = load_config()
has_credentials = bool(config.get("username") and config.get("password"))
if has_credentials:
    st.sidebar.success(f"Instagram Account Configured: @{config.get('username')}")
else:
    st.sidebar.warning("No Instagram credentials in .env (Dry-run posting supported)")

st.sidebar.markdown("---")
st.sidebar.subheader("Quick Benchmarking")
if st.sidebar.button("📥 Load Multilingual Benchmark Cases"):
    sample_path = Path(__file__).parent / "test_captions.csv"
    if sample_path.exists():
        with open(sample_path, "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        
        # Pre-populate sample posts
        st.session_state["raw_input"] = "\n".join([
            f"https://www.instagram.com/p/test_{r['id']}/ :: {r['caption']}"
            for r in reader[:6]
        ])
        st.sidebar.success("Loaded 6 multilingual benchmark samples!")

# ---------------------------------------------------------
# Main Header
# ---------------------------------------------------------
st.title("🇮🇳 Armed Forces Information Review & Triage System")
st.caption("Human-in-the-Loop Social Verification, Multilingual Sentiment Analysis, RAG Fact-Checking & Official Response Management")

# Initialize Session State
if "processed_posts" not in st.session_state:
    st.session_state["processed_posts"] = []
if "clusters" not in st.session_state:
    st.session_state["clusters"] = []

tab_triage, tab_analytics, tab_kb, tab_sitrep = st.tabs([
    "📋 Triage & Publishing Station",
    "📊 Narrative Analytics & Clusters",
    "🛡️ PIB Knowledge Base Explorer",
    "📑 Situation Report (SitRep)",
])

# =========================================================
# TAB 1: TRIAGE & PUBLISHING STATION (HUMAN-IN-THE-LOOP)
# =========================================================
with tab_triage:
    st.subheader("1. Ingest Social Media Posts")
    
    default_text = st.session_state.get(
        "raw_input",
        "https://www.instagram.com/p/C7Sample123/\nhttps://www.instagram.com/p/C7Sample456/"
    )
    
    input_text = st.text_area(
        "Enter Instagram URLs or Post Captions (one per line or formatted as URL :: Caption):",
        value=default_text,
        height=110,
        help="Paste target Instagram post links. For local testing without scraping, format as: URL :: Caption text",
    )

    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        run_batch = st.button("🚀 Run Analysis Batch", type="primary", use_container_width=True)
    with col_btn2:
        if st.button("Clear Results"):
            st.session_state["processed_posts"] = []
            st.session_state["clusters"] = []
            st.rerun()

    if run_batch:
        lines = [line.strip() for line in input_text.splitlines() if line.strip()]
        if not lines:
            st.error("Please enter at least one URL or post caption.")
        else:
            new_results = []
            progress_bar = st.progress(0, text="Initializing analysis pipeline...")

            for idx, line in enumerate(lines):
                progress = (idx + 1) / len(lines)
                progress_bar.progress(progress, text=f"Analyzing item {idx+1} of {len(lines)}...")

                # Parse formatted simulation: "URL :: Caption" or direct URL
                if "::" in line:
                    parts = line.split("::", 1)
                    url = parts[0].strip()
                    caption = parts[1].strip()
                    post_data = {
                        "url": url,
                        "username": "monitored_user",
                        "caption": caption,
                        "media_id": url.split("/")[-2] if "/p/" in url else f"post_{idx}",
                        "image_url": "",
                        "ocr_text": "",
                    }
                    try:
                        res = analyze_post_content(post_data, model=model)
                        res["item_id"] = idx
                        new_results.append(res)
                    except Exception as exc:
                        st.error(f"Error analyzing item {idx+1}: {exc}")

                elif line.startswith("http://") or line.startswith("https://"):
                    # Live Instagram URL
                    try:
                        res = analyze_instagram_url(line, model=model, headless=True)
                        res["item_id"] = idx
                        new_results.append(res)
                    except Exception as exc:
                        st.warning(f"Could not scrape {line} (Fallback to offline review): {exc}")
                else:
                    # Treat raw text as caption
                    post_data = {
                        "url": f"https://www.instagram.com/p/offline_post_{idx}/",
                        "username": "direct_input",
                        "caption": line,
                        "media_id": f"offline_{idx}",
                        "image_url": "",
                        "ocr_text": "",
                    }
                    try:
                        res = analyze_post_content(post_data, model=model)
                        res["item_id"] = idx
                        new_results.append(res)
                    except Exception as exc:
                        st.error(f"Error analyzing text: {exc}")

            progress_bar.empty()
            st.session_state["processed_posts"] = new_results

            # Run narrative clustering immediately
            if new_results:
                clustered = cluster_posts(new_results)
                st.session_state["clusters"] = clustered.get("clusters", [])
                st.session_state["processed_posts"] = clustered.get("posts", new_results)

            st.success(f"Processed {len(new_results)} items successfully!")
            st.rerun()

    # Display Processed Review Queue
    processed = st.session_state.get("processed_posts", [])
    if not processed:
        st.info("No posts in the triage queue. Enter links above or click 'Load Multilingual Benchmark Cases' in the sidebar.")
    else:
        st.markdown("---")
        st.subheader(f"2. Human Review & Response Approval Queue ({len(processed)} posts)")

        for index, item in enumerate(processed):
            post = item.get("post", {})
            analysis = item.get("analysis", {})
            verification = item.get("verification", [])
            approval_status = item.get("approval_status", "PENDING")
            cluster_label = item.get("cluster_label", "General")

            # Determine badge styling
            sent = analysis.get("sentiment", "NEUTRAL")
            sent_color = "🔴" if sent == "NEGATIVE" else ("🟢" if sent == "POSITIVE" else "⚪")

            with st.container(border=True):
                top_col1, top_col2 = st.columns([3, 1])
                with top_col1:
                    st.markdown(f"### Post #{index+1} — {post.get('url', 'N/A')}")
                    st.caption(f"Author: @{post.get('username', 'user')} | Assigned Cluster: **{cluster_label}**")
                with top_col2:
                    if approval_status == "APPROVED":
                        st.success("STATUS: APPROVED / POSTED")
                    elif approval_status == "REJECTED":
                        st.error("STATUS: REJECTED")
                    else:
                        st.warning("STATUS: PENDING REVIEW")

                # Metrics Row
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Category", analysis.get("category", "NOT_ARMY_RELATED"))
                m2.metric("Sentiment", f"{sent_color} {sent}")
                m3.metric("Language", analysis.get("language", "Other"))
                m4.metric("Claims Extracted", len(analysis.get("claims", [])))

                # Post Caption & OCR Image Text
                st.markdown("**Original Caption:**")
                st.text_area("Caption Content", value=post.get("caption", ""), height=80, disabled=True, key=f"cap_{index}")

                if post.get("ocr_text"):
                    st.markdown("**🖼️ Extracted Text from Image/Meme (OCR):**")
                    st.info(post["ocr_text"])

                # Fact-Checking Section
                if verification:
                    st.markdown("**🛡️ Government Source Verification (PIB & MoD Grounding):**")
                    for v_idx, v in enumerate(verification, start=1):
                        v_status = v.get("status", "UNVERIFIED")
                        v_badge = "❌ DEBUNKED / CONTRADICTED" if v_status == "CONTRADICTED" else (
                            "✅ CONFIRMED / SUPPORTED" if v_status == "SUPPORTED" else "⚠️ UNVERIFIED"
                        )
                        st.markdown(f"- **Claim {v_idx}:** *\"{v.get('claim')}\"* — **{v_badge}**")
                        if v.get("source_name"):
                            st.caption(f"Source: {v['source_name']} | Reference: [{v.get('source_url', 'Portal')}]({v.get('source_url', '#')})")
                        if v.get("evidence"):
                            with st.expander(f"View Official Evidence for Claim {v_idx}"):
                                st.write(v["evidence"])

                # Editable AI Draft
                st.markdown("**Suggested Counter-Response Draft (Edit before approval):**")
                current_draft = item.get("response", "")
                edited_draft = st.text_area(
                    "Edit Response",
                    value=current_draft,
                    height=90,
                    key=f"draft_{index}",
                    disabled=(approval_status == "APPROVED"),
                )
                item["response"] = edited_draft

                # Human-In-The-Loop Action Buttons
                btn_col1, btn_col2, btn_col3 = st.columns([2, 1, 3])
                with btn_col1:
                    if st.button("✅ Approve & Post via Official ID", key=f"approve_{index}", type="primary", disabled=(approval_status == "APPROVED")):
                        with st.spinner("Authorizing and submitting approved comment..."):
                            success, msg = publish_approved_comment(
                                post_url=post.get("url", ""),
                                comment_text=edited_draft,
                                dry_run=dry_run,
                            )
                            if success:
                                item["approval_status"] = "APPROVED"
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(f"Failed to post: {msg}")

                with btn_col2:
                    if st.button("❌ Reject", key=f"reject_{index}", disabled=(approval_status == "REJECTED")):
                        item["approval_status"] = "REJECTED"
                        st.warning("Post response rejected.")
                        st.rerun()

                with btn_col3:
                    if approval_status != "PENDING":
                        if st.button("↺ Reset to Pending", key=f"reset_{index}"):
                            item["approval_status"] = "PENDING"
                            st.rerun()


# =========================================================
# TAB 2: NARRATIVE ANALYTICS & CLUSTERS
# =========================================================
with tab_analytics:
    st.subheader("Narrative & Trend Intelligence")
    processed = st.session_state.get("processed_posts", [])
    clusters = st.session_state.get("clusters", [])

    if not processed:
        st.info("No data available. Process posts in the Triage tab to view narrative analytics.")
    else:
        # Overview Metrics
        total = len(processed)
        negs = sum(1 for p in processed if (p.get("analysis") or {}).get("sentiment") == "NEGATIVE")
        pos = sum(1 for p in processed if (p.get("analysis") or {}).get("sentiment") == "POSITIVE")
        neut = total - (negs + pos)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Monitored", total)
        c2.metric("Negative / Critical", negs, delta=f"{(negs/total)*100:.0f}%", delta_color="inverse")
        c3.metric("Positive Sentiment", pos, delta=f"{(pos/total)*100:.0f}%")
        c4.metric("Neutral Posts", neut)

        st.markdown("---")
        st.subheader("Emerging Narrative Clusters (Topic Modeling)")
        st.caption("Automatically generated via TF-IDF Vectorization and K-Means clustering across post captions and factual claims.")

        if clusters:
            for c in clusters:
                with st.container(border=True):
                    col_c1, col_c2 = st.columns([3, 1])
                    with col_c1:
                        st.markdown(f"#### 🏷️ {c['label']}")
                        st.write(f"**Key Topic Keywords:** `{'`, `'.join(c.get('keywords', []))}`")
                    with col_c2:
                        st.metric("Cluster Volume", f"{c['count']} posts")
                    
                    st.caption("Sentiment breakdown within cluster: " + ", ".join([f"{k}: {v}" for k, v in c.get("sentiment_breakdown", {}).items()]))
        else:
            st.info("Insufficient post volume for multi-cluster segmentation.")


# =========================================================
# TAB 3: PIB & OFFICIAL KNOWLEDGE BASE EXPLORER
# =========================================================
with tab_kb:
    st.subheader("Official Fact-Check & Verification Knowledge Base")
    st.caption("Curated repository of official Press Information Bureau (PIB) fact checks, Ministry of Defence notices, and ADGPI clarifications used for RAG grounding.")

    kb_entries = load_knowledge_base()
    search_query = st.text_input("🔍 Search Knowledge Base by keyword or topic:", "")

    filtered = [
        e for e in kb_entries
        if search_query.lower() in e["topic"].lower()
        or search_query.lower() in e["claim_pattern"].lower()
        or any(search_query.lower() in k.lower() for k in e.get("keywords", []))
    ] if search_query else kb_entries

    for entry in filtered:
        with st.container(border=True):
            e_col1, e_col2 = st.columns([3, 1])
            with e_col1:
                st.markdown(f"#### [{entry['id']}] {entry['topic']}")
                st.write(f"**Addressed Rumor:** {entry['claim_pattern']}")
                st.markdown(f"**Official Verification Evidence:**\n> {entry['evidence']}")
                st.caption(f"Authority: **{entry['source_name']}** | Published: {entry.get('date', 'N/A')}")
            with e_col2:
                status_color = "red" if entry["official_status"] == "CONTRADICTED" else "green"
                st.markdown(f"**Status:** :{status_color}[{entry['official_status']}]")
                if entry.get("source_url"):
                    st.link_button("View Source", entry["source_url"])


# =========================================================
# TAB 4: SITUATION REPORT (SITREP)
# =========================================================
with tab_sitrep:
    st.subheader("Automated Daily Situation Report (SitRep)")
    st.caption("Executive briefing generated dynamically from the current monitoring, verification, and triage cycle.")

    processed = st.session_state.get("processed_posts", [])
    clusters = st.session_state.get("clusters", [])

    if not processed:
        st.info("No posts have been processed yet. Ingest and review posts to generate a Situation Report.")
    else:
        sitrep_content = generate_sitrep(processed_posts=processed, clusters=clusters)

        # Download Button
        today_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        st.download_button(
            label="📥 Download SitRep (.md)",
            data=sitrep_content,
            file_name=f"SitRep_IndianArmy_{today_slug}.md",
            mime="text/markdown",
            type="primary",
        )

        st.markdown("---")
        st.markdown(sitrep_content)