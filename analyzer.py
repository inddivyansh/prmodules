from __future__ import annotations

import json
import logging

import ollama


ANALYZER_PROMPT = """
You are analyzing a social media post (e.g. Instagram caption or OCR image text) for an Indian Army public-information review workflow.

Analyze the text and return ONLY valid JSON.

Required JSON format:

{{
  "category": "ARMY_RELATED" or "NOT_ARMY_RELATED",
  "sentiment": "POSITIVE" or "NEGATIVE" or "NEUTRAL",
  "language": "English" or "Hindi" or "Hinglish" or "Urdu" or "Other",
  "claims": [
    "specific factual claim 1",
    "specific factual claim 2"
  ]
}}

Rules:

1. ARMY_RELATED means the post is discussing, mentioning, or alleging anything about
   the Indian Army, Indian Armed Forces, soldiers, border operations, casualties,
   or recruitment schemes (e.g. Agnipath, Agniveer)—REGARDLESS of whether the tone is positive, negative, or critical.

2. NOT_ARMY_RELATED means the post has nothing to do with the armed forces or military
   (e.g., sports, weather, food, general lifestyle, civilian politics).

3. Sentiment:
   POSITIVE = favorable, patriotic, supportive, or praising rescue/security efforts.
   NEGATIVE = critical, accusatory, hostile, alleging human rights violations, or spreading unverified rumors damaging reputation.
   NEUTRAL = factual news reporting, informational query, or ambiguous tone.

4. Language Detection:
   - "English": Standard English vocabulary and grammar (even when mentioning Indian entities, acronyms like UPI, or places).
   - "Hindi": Hindi written in Devanagari script (e.g. भारतीय सेना).
   - "Hinglish": Hindi or Urdu spoken sentences transliterated in Roman/Latin script (e.g. "bhavishya barbaad ho raha hai", "logo ki jaan bachai").
   - "Urdu": Urdu text in Perso-Arabic script or formal Urdu vocabulary.
   - "Other": Any other language.

5. Extract factual CLAIMS, not keywords or topics:
   - Every claim must be a complete, testable statement checkable against official sources.
   - Example 1: "The Indian Army constructed a temporary Bailey bridge in Wayanad." -> Claim: "The Indian Army constructed a temporary Bailey bridge in Wayanad."
   - Example 2: "Agniveers ko koi pension ya insurance nahi milta." -> Claim: "Agniveers do not receive pension or insurance benefits."
   - Do NOT return vague single-word topics like "army", "kashmir", "rescue".
   - If there is no specific testable factual claim, return an empty list [].

Text to analyze:

{caption}

Return ONLY valid JSON:
"""


def analyze_caption(caption: str, model: str) -> dict:
    """Analyze an Instagram caption (and optional OCR text) using Ollama."""
    prompt = ANALYZER_PROMPT.format(caption=caption)

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            options={"num_ctx": 2048, "temperature": 0.1},
        )

        raw = response["message"]["content"].strip()

        # Remove Markdown code fences if Ollama adds them.
        if raw.startswith("```json"):
            raw = raw[len("```json"):].strip()
        if raw.startswith("```"):
            raw = raw[3:].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

        result = json.loads(raw)

        # Normalize category
        category = result.get("category", "NOT_ARMY_RELATED").upper()
        if "ARMY" in category and "NOT" not in category:
            category = "ARMY_RELATED"
        else:
            category = "NOT_ARMY_RELATED"

        # Normalize sentiment
        sentiment = result.get("sentiment", "NEUTRAL").upper()
        if sentiment not in {"POSITIVE", "NEGATIVE", "NEUTRAL"}:
            sentiment = "NEUTRAL"

        # Normalize language
        language = result.get("language", "Other").capitalize()
        if language not in {"English", "Hindi", "Hinglish", "Urdu", "Other"}:
            language = "Other"

        claims = result.get("claims", [])
        if not isinstance(claims, list):
            claims = []

        return {
            "category": category,
            "sentiment": sentiment,
            "language": language,
            "claims": [str(c).strip() for c in claims if str(c).strip()],
        }

    except json.JSONDecodeError as exc:
        logging.error("Ollama returned invalid JSON: %s", exc)
        return {
            "category": "NOT_ARMY_RELATED",
            "sentiment": "NEUTRAL",
            "language": "Other",
            "claims": [],
        }

    except Exception as exc:
        logging.exception("Analyzer failed: %s", exc)
        return {
            "category": "NOT_ARMY_RELATED",
            "sentiment": "NEUTRAL",
            "language": "Other",
            "claims": [],
        }