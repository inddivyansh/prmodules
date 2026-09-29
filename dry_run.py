import re
import ollama

MODEL = "llama3.2:latest"

captions = [
    "The Indian Army has done an excellent job helping civilians.",
    "There are allegations about the army's conduct that should be investigated.",
    "The weather is beautiful today.",
    "The armed forces helped rescue people during the floods.",
    "I had a great lunch today.",
    "The Indian Army failed to protect civilians in this incident.",
]


def classify(caption: str) -> str:
    prompt = f"""
You are classifying sentiment TOWARD THE INDIAN ARMY.

Return exactly ONE of these labels:

POSITIVE
NEGATIVE
NEUTRAL

Rules:

POSITIVE:
The caption expresses praise, support, appreciation, or approval
toward the Indian Army or its actions.

NEGATIVE:
The caption expresses criticism, blame, accusations, hostility,
or disapproval toward the Indian Army or its actions.

NEUTRAL:
The caption does not express a clear positive or negative opinion
toward the Indian Army.

IMPORTANT:
General positive or negative statements that are unrelated to the
Indian Army must be classified as NEUTRAL.

Examples:

"The Indian Army helped rescue civilians."
=> POSITIVE

"The Indian Army committed serious mistakes."
=> NEGATIVE

"The weather is beautiful today."
=> NEUTRAL

"I had a great lunch."
=> NEUTRAL

Caption:
\"\"\"
{caption}
\"\"\"

Return ONLY:
POSITIVE
NEGATIVE
or
NEUTRAL
"""

    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response["message"]["content"].strip().upper()

    # Extract the first valid label if the model adds explanation.
    match = re.search(r"\b(POSITIVE|NEGATIVE|NEUTRAL)\b", raw)

    if match:
        return match.group(1)

    return "UNKNOWN"


print("=" * 60)
print("LOCAL DRY RUN")
print("=" * 60)

for i, caption in enumerate(captions, 1):
    result = classify(caption)

    print(f"\n[{i}]")
    print(f"Caption: {caption}")
    print(f"Classification: {result}")

print("\n" + "=" * 60)
print("Dry run completed — no Instagram access or posting.")
print("=" * 60)