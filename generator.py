from __future__ import annotations

import logging

import ollama


GENERATOR_PROMPT = """
You are drafting a response for an Indian Army public-information review workflow.

The response will be reviewed by a human before publication.

Original Instagram post:
{caption}

Post analysis:
Category: {category}
Sentiment: {sentiment}
Language: {language}

Official government verification:

{verification}

Write ONE concise response in the same language as the original post.

Requirements:

- Present the official information clearly.
- Correct a factual claim when official evidence directly contradicts it.
- Where a claim is supported by an official source, accurately reflect that
  information.
- If a claim is UNVERIFIED, do not state that it is false.
- Do not invent facts, figures, events, quotations, or sources.
- Do not insult or attack the person who made the post.
- Keep the response respectful and professional.
- The response should be suitable for human public-relations review.
- Prefer concrete official information over generic statements.
- Keep the response below 300 characters.

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