import re

ANGER_WORDS = {
    "angry", "mad", "furious", "pissed", "annoyed",
    "irritated", "frustrated", "upset", "livid",
}

NEGATIVE_HINTS = {
    "not happy", "unacceptable", "terrible", "awful", "horrible",
    "disappointed", "worst", "bad experience", "complaint",
}

POSITIVE_HINTS = {
    "thank you", "thanks", "great", "awesome", "perfect",
    "amazing", "love", "appreciate", "helpful",
}

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())

def rule_based_sentiment(text: str) -> str:
    t = _norm(text)
    if not t:
        return "neutral"

    if any(w in t for w in ANGER_WORDS):
        return "negative"
    if any(p in t for p in NEGATIVE_HINTS):
        return "negative"
    if any(p in t for p in POSITIVE_HINTS):
        return "positive"

    return "neutral"
