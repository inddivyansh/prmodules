from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def generate_sitrep(
    processed_posts: list[dict[str, Any]],
    clusters: list[dict[str, Any]] | None = None,
) -> str:
    """Generate a formal Situation Report (SitRep) markdown document from reviewed social media posts."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_posts = len(processed_posts)

    # Sentiment distribution
    sentiments = []
    categories = []
    languages = []
    verified_claims = []
    approved_count = 0
    rejected_count = 0
    pending_count = 0

    for item in processed_posts:
        analysis = item.get("analysis", {})
        post_data = item.get("post", {})
        verifications = item.get("verification", [])
        status = item.get("approval_status", "PENDING")

        if status == "APPROVED":
            approved_count += 1
        elif status == "REJECTED":
            rejected_count += 1
        else:
            pending_count += 1

        sentiments.append(analysis.get("sentiment", "NEUTRAL"))
        categories.append(analysis.get("category", "NOT_ARMY_RELATED"))
        languages.append(analysis.get("language", "Other"))

        for v in verifications:
            if v.get("status") in {"CONTRADICTED", "SUPPORTED"}:
                verified_claims.append(v)

    sent_counts = Counter(sentiments)
    cat_counts = Counter(categories)
    lang_counts = Counter(languages)

    md = []
    md.append(f"# 🇮🇳 DAILY SITUATION REPORT (SITREP) — SOCIAL INFORMATION ENVIRONMENT")
    md.append(f"**Report Generated:** {now_str}")
    md.append(f"**Classification:** RESTRICTED // FOR PR & VERIFICATION OFFICERS ONLY")
    md.append(f"**Monitoring Scope:** Instagram Public Relations & Misinformation Triage")
    md.append("\n---\n")

    md.append("## 1. EXECUTIVE SUMMARY")
    md.append(f"- **Total Posts Processed:** {total_posts}")
    md.append(f"- **Army-Related Topics:** {cat_counts.get('ARMY_RELATED', 0)} | **Unrelated/Filtered:** {cat_counts.get('NOT_ARMY_RELATED', 0)}")
    md.append(f"- **Sentiment Distribution:**")
    md.append(f"  - 🔴 **Negative / Critical:** {sent_counts.get('NEGATIVE', 0)}")
    md.append(f"  - 🟢 **Positive / Supportive:** {sent_counts.get('POSITIVE', 0)}")
    md.append(f"  - ⚪ **Neutral / Informational:** {sent_counts.get('NEUTRAL', 0)}")
    md.append(f"- **Languages Detected:** {', '.join([f'{k}: {v}' for k, v in lang_counts.items()])}")
    md.append(f"- **Triage Actions:** Approved: {approved_count} | Pending: {pending_count} | Rejected: {rejected_count}")
    md.append("\n---\n")

    md.append("## 2. KEY EMERGING NARRATIVE CLUSTERS")
    if clusters:
        for c in clusters:
            kw = ", ".join(c.get("keywords", []))
            sents = ", ".join([f"{k}: {v}" for k, v in c.get("sentiment_breakdown", {}).items()])
            md.append(f"### {c.get('label', 'Cluster')}")
            md.append(f"- **Volume:** {c.get('count', 0)} posts")
            md.append(f"- **Core Keywords:** `{kw}`")
            md.append(f"- **Sentiment Tone:** {sents if sents else 'Balanced'}")
    else:
        md.append("_No automated narrative clusters generated for this batch._")
    md.append("\n---\n")

    md.append("## 3. FACT-CHECKED CLAIMS & OFFICIAL VERIFICATION")
    if verified_claims:
        for idx, vc in enumerate(verified_claims, start=1):
            badge = "❌ DEBUNKED / CONTRADICTED" if vc["status"] == "CONTRADICTED" else "✅ CONFIRMED / SUPPORTED"
            md.append(f"#### {idx}. Claim: *\"{vc['claim']}\"*")
            md.append(f"- **Official Verdict:** **{badge}**")
            md.append(f"- **Authority Source:** {vc.get('source_name', 'Official Portal')}")
            if vc.get("source_url"):
                md.append(f"- **Reference URL:** {vc['source_url']}")
            md.append(f"- **Evidence Summary:** {vc.get('evidence', '')[:250]}...")
            md.append("")
    else:
        md.append("_No high-risk factual claims required formal contradiction or confirmation in this monitoring cycle._")
    md.append("\n---\n")

    md.append("## 4. PUBLIC RESPONSE RECORD")
    approved_posts = [p for p in processed_posts if p.get("approval_status") == "APPROVED"]
    if approved_posts:
        for p in approved_posts:
            post_meta = p.get("post", {})
            md.append(f"- **Target URL:** {post_meta.get('url', 'N/A')}")
            md.append(f"  - **Author:** @{post_meta.get('username', 'user')}")
            md.append(f"  - **Official Approved Statement:**\n    > \"{p.get('response', '')}\"")
    else:
        md.append("_No responses were approved for publication in this batch._")
    md.append("\n---\n")

    md.append("## 5. STRATEGIC RECOMMENDATIONS")
    if sent_counts.get("NEGATIVE", 0) > 0:
        md.append("- **High Vigilance Advised:** Monitor emerging hashtag variations around debunked rumors.")
        md.append("- **Proactive Press Release:** Disseminate approved PIB advisory link across official social handles.")
    else:
        md.append("- **Stable Environment:** Continue passive monitoring of high-traffic military discussion tags.")

    return "\n".join(md)
