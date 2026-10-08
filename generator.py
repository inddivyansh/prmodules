from __future__ import annotations

import logging

import ollama


GENERATOR_PROMPT = """
You are drafting a patriotic response defending the Indian Armed Forces against misleading narratives and propaganda.

Original Instagram post:
{caption}

Post analysis:
Category: {category}
Sentiment: {sentiment}
Language: {language}

Official government verification:
{verification}

Write ONE concise, patriotic comment in the same language as the original post.

Requirements:
- Stand firmly with the Indian Army ("Indian Army is always in service of the nation and protects all citizens with supreme sacrifice").
- Do NOT stay neutral: passionately defend the honour, discipline, and integrity of our soldiers.
- Directly call out fake news, fabricated allegations, and unverified anti-army claims ("This is completely fake news / propaganda, do not fall for baseless rumours").
- Urge readers to trust only official Armed Forces communications (PIB Defence, ADGPI, MoD).
- Maintain high patriotic morale and pride in our jawans ("Jai Hind 🇮🇳", "Proud of our Indian Army").
- Keep the response below 280 characters.

Return ONLY the proposed response text.
"""


def generate_response(
    caption: str,
    analysis: dict,
    verification_results: list[dict],
    model: str,
) -> str:
    """Generate a grounded response draft using Ollama."""

    verification_text_parts = []

    for result in verification_results:
        verification_text_parts.append(
            f"""
Claim:
{result["claim"]}

Status:
{result["status"]}

Source:
{result["source_name"]}

URL:
{result["source_url"]}

Official evidence:
{result["evidence"]}

Reason:
{result["reason"]}
"""
        )

    verification_text = "\n".join(verification_text_parts)

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": GENERATOR_PROMPT.format(
                        caption=caption,
                        category=analysis.get("category", ""),
                        sentiment=analysis.get("sentiment", ""),
                        language=analysis.get("language", ""),
                        verification=verification_text,
                    ),
                }
            ],
            options={"num_ctx": 2048, "temperature": 0.2},
        )

        text = response["message"]["content"].strip()

        if len(text) > 300:
            text = text[:300].rsplit(" ", 1)[0].rstrip(".,;: ") + "."

        return text

    except Exception as exc:
        logging.exception("Response generation failed: %s", exc)
        return ""