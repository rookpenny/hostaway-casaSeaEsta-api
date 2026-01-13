import re
import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

NEGATIVE_MARKERS = [
    "angry", "mad", "furious", "upset", "frustrated", "annoyed",
    "terrible", "awful", "horrible", "bad", "disappointed",
    "refund", "complaint", "unacceptable", "worst",
]

POSITIVE_MARKERS = [
    "great", "amazing", "awesome", "love", "fantastic", "perfect", "wonderful",
]

def classify_sentiment_rule(text: str) -> str:
    """
    Deterministic fallback sentiment classifier.
    Returns: negative | positive | neutral
    """
    t = (text or "").lower()

    if any(w in t for w in NEGATIVE_MARKERS):
        return "negative"
    if any(w in t for w in POSITIVE_MARKERS):
        return "positive"
    return "neutral"


def classify_sentiment_openai(client: OpenAI, text: str) -> str:
    """
    OpenAI-first sentiment classifier.
    Returns: negative | neutral | positive
    Raises if OpenAI call fails.
    """
    model = (os.getenv("OPENAI_SENTIMENT_MODEL") or os.getenv("OPENAI_CHAT_MODEL") or "gpt-4o-mini").strip()

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": "Classify the user's sentiment as exactly one label: negative, neutral, or positive. Output ONLY the label.",
            },
            {"role": "user", "content": text or ""},
        ],
    )

    label = (resp.choices[0].message.content or "").strip().lower()
    if label not in {"negative", "neutral", "positive"}:
        return "neutral"
    return label


def classify_sentiment_with_fallback(client: OpenAI, text: str) -> str:
    """
    Uses OpenAI first. If it fails, falls back to rule-based.
    """
    try:
        return classify_sentiment_openai(client, text)
    except Exception as e:
        logger.warning("Sentiment OpenAI failed, using rule fallback: %r", e)
        return classify_sentiment_rule(text)
