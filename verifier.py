from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import ollama
from selenium.webdriver.common.by import By

from shared import pause

KB_PATH = Path(__file__).parent / "knowledge_base.json"

OFFICIAL_SOURCES = {
    "PIB": [
        "pib.gov.in",
    ],
    "Ministry of Defence": [
        "mod.gov.in",
    ],
    "Indian Army": [
        "indianarmy.gov.in",
        "indianarmy.nic.in",
    ],
}

VERIFICATION_PROMPT = """
You are verifying a factual claim against official government evidence.

CLAIM:
{claim}

OFFICIAL SOURCE:
{source_name}

OFFICIAL SOURCE URL:
{source_url}

OFFICIAL SOURCE EVIDENCE:
{source_text}

Determine whether the official source supports or contradicts the claim.

Return ONLY valid JSON:

{{
  "status": "SUPPORTED" or "CONTRADICTED" or "PARTIALLY_SUPPORTED" or "UNVERIFIED",
  "reason": "brief explanation"
}}

Rules:
- SUPPORTED: The official source provides information confirming the claim.
- CONTRADICTED: The official source directly conflicts with or debunks the claim.
- PARTIALLY_SUPPORTED: Only part of the claim is supported.
- UNVERIFIED: The official source does not provide enough evidence to confirm or debunk.
- Do not invent facts or use unstated assumptions.
"""


def load_knowledge_base() -> list[dict]:
    """Load the local verified knowledge base."""
    if not KB_PATH.exists():
        return []
    try:
        with open(KB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logging.error("Failed to load knowledge base: %s", exc)
        return []


def match_knowledge_base(claim: str) -> dict | None:
    """Retrieve the most relevant knowledge base record for a given claim via keyword matching."""
    kb = load_knowledge_base()
    if not kb:
        return None

    claim_lower = claim.lower()
    best_entry = None
    best_score = 0

    for entry in kb:
        score = 0
        keywords = entry.get("keywords", [])
        for kw in keywords:
            if kw.lower() in claim_lower:
                score += 2
        # Topic overlap
        topic = entry.get("topic", "").lower()
        for word in topic.split():
            if len(word) > 3 and word in claim_lower:
                score += 1

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_score >= 2 and best_entry:
        return best_entry

    return None


def is_allowed_url(url: str) -> bool:
    """Check whether a URL belongs to an approved official domain."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return False
        hostname = hostname.lower()
        for domains in OFFICIAL_SOURCES.values():
            for domain in domains:
                domain = domain.lower()
                if hostname == domain or hostname.endswith("." + domain):
                    return True
        return False
    except Exception:
        return False


def get_source_name(url: str) -> str:
    """Return configured source name for an official URL."""
    hostname = (urlparse(url).hostname or "").lower()
    for source_name, domains in OFFICIAL_SOURCES.items():
        for domain in domains:
            domain = domain.lower()
            if hostname == domain or hostname.endswith("." + domain):
                return source_name
    return ""


def extract_real_url(href: str) -> str | None:
    """Extract destination URL from a link or Google redirect."""
    if not href:
        return None
    if is_allowed_url(href):
        return href
    parsed = urlparse(href)
    if parsed.hostname and parsed.hostname.lower() in {"www.google.com", "google.com"}:
        params = parse_qs(parsed.query)
        redirected_url = params.get("q", [None])[0]
        if redirected_url and is_allowed_url(redirected_url):
            return redirected_url
    return None


def extract_page_text(driver, url: str) -> str:
    """Extract clean text content from a web page using Selenium."""
    if not driver:
        return ""
    try:
        driver.get(url)
        pause(2)
        body = driver.find_element(By.TAG_NAME, "body")
        return body.text.strip()[:4000]
    except Exception as exc:
        logging.warning("Could not extract text from %s: %s", url, exc)
        return ""


def search_official_sources(driver, claim: str, max_results_per_source: int = 1) -> list[dict]:
    """Search Google restricted to approved official domains."""
    if not driver:
        return []

    candidates = []
    seen_urls = set()

    for source_name, domains in OFFICIAL_SOURCES.items():
        for domain in domains:
            query = f'site:{domain} "{claim[:160]}"'
            search_url = "https://www.google.com/search?q=" + quote(query)
            try:
                driver.get(search_url)
                pause(2)
                anchors = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                count = 0
                for anchor in anchors:
                    href = anchor.get_attribute("href")
                    real_url = extract_real_url(href)
                    if not real_url or real_url in seen_urls:
                        continue
                    seen_urls.add(real_url)
                    candidates.append({"source_name": source_name, "url": real_url})
                    count += 1
                    if count >= max_results_per_source:
                        break
            except Exception as exc:
                logging.warning("Could not search %s: %s", domain, exc)

    return candidates


def evaluate_claim(
    claim: str,
    source_name: str,
    source_url: str,
    source_text: str,
    model: str,
) -> dict:
    """Ask Ollama to compare a claim with official evidence."""
    prompt = VERIFICATION_PROMPT.format(
        claim=claim,
        source_name=source_name,
        source_url=source_url,
        source_text=source_text,
    )

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048, "temperature": 0.0},
        )
        raw = response["message"]["content"].strip()
        if raw.startswith("```json"):
            raw = raw[len("```json"):].strip()
        if raw.startswith("```"):
            raw = raw[3:].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

        result = json.loads(raw)
        return {
            "status": result.get("status", "UNVERIFIED"),
            "reason": result.get("reason", ""),
        }
    except Exception as exc:
        logging.warning("Ollama verification evaluation failed: %s", exc)
        return {
            "status": "UNVERIFIED",
            "reason": "Verification could not be fully parsed by local LLM.",
        }


def verify_claim(driver, claim: str, model: str) -> dict:
    """Verify one claim: first check local Knowledge Base (RAG), then fallback to search."""
    # 1. Local RAG lookup in verified Knowledge Base
    kb_match = match_knowledge_base(claim)
    if kb_match:
        logging.info("Matched local knowledge base entry: %s", kb_match["id"])
        # Evaluate with Ollama against the exact KB evidence
        evaluation = evaluate_claim(
            claim=claim,
            source_name=kb_match["source_name"],
            source_url=kb_match["source_url"],
            source_text=kb_match["evidence"],
            model=model,
        )
        # Fall back to known official_status if evaluation was inconclusive
        status = evaluation["status"]
        if status == "UNVERIFIED" and kb_match.get("official_status"):
            status = kb_match["official_status"]

        return {
            "claim": claim,
            "status": status,
            "reason": evaluation.get("reason") or f"Matched verified advisory {kb_match['id']}.",
            "source_name": kb_match["source_name"],
            "source_url": kb_match["source_url"],
            "evidence": kb_match["evidence"],
            "matched_kb_id": kb_match["id"],
        }

    # 2. Live Web Search fallback if driver is available
    if driver:
        candidates = search_official_sources(driver, claim)
        if candidates:
            evaluations = []
            for candidate in candidates:
                source_text = extract_page_text(driver, candidate["url"])
                if not source_text:
                    continue
                ev = evaluate_claim(
                    claim=claim,
                    source_name=candidate["source_name"],
                    source_url=candidate["url"],
                    source_text=source_text,
                    model=model,
                )
                evaluations.append({
                    "claim": claim,
                    "status": ev["status"],
                    "reason": ev["reason"],
                    "source_name": candidate["source_name"],
                    "source_url": candidate["url"],
                    "evidence": source_text[:2000],
                })
            if evaluations:
                priority = {"CONTRADICTED": 4, "SUPPORTED": 3, "PARTIALLY_SUPPORTED": 2, "UNVERIFIED": 1}
                return max(evaluations, key=lambda item: priority.get(item["status"], 0))

    return {
        "claim": claim,
        "status": "UNVERIFIED",
        "reason": "No matching record found in official fact-check knowledge base.",
        "source_name": "",
        "source_url": "",
        "evidence": "",
    }


def verify_claims(driver, claims: list[str], model: str) -> list[dict]:
    """Verify all extracted claims."""
    return [verify_claim(driver=driver, claim=c, model=model) for c in claims]