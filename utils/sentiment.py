# utils/sentiment.py
import os
import json
import re
from typing import Any, Dict, List, Tuple

# -----------------------------
# Patterns (fast deterministic)
# -----------------------------

# Explicit "reset" / de-escalation phrases
_RESET_RE = re.compile(
    r"\b(jk|j/k|just kidding|kidding|i was joking|only joking|never mind|nevermind|all good|no worries|we're good|we are good)\b",
    re.I,
)

# Laugh / playful markers that often mean the guest is NOT upset
_PLAYFUL_RE = re.compile(
    r"(\b(lol|lmao|rofl|haha|hehe)\b|😂|🤣|😅|😉|😜|😆)",
    re.I,
)

# Strong negative escalation markers
_ESCALATE_RE = re.compile(
    r"\b(refund|chargeback|lawsuit|report you|unsafe|dangerous|mold|infestation|bed ?bugs|police|fraud|scam)\b",
    re.I,
)

# Quick keyword lists for fallback
NEGATIVE_WORDS = [
    "terrible", "awful", "angry", "bad", "disappointed", "upset", "mad", "furious",
    "annoyed", "frustrated", "confused", "ridiculous", "unacceptable", "worst",
]
POSITIVE_WORDS = [
    "great", "amazing", "awesome", "love", "fantastic", "perfect", "thank you", "thanks",
    "appreciate", "wonderful",
]

MOODS = {"happy", "calm", "confused", "upset", "angry", "anxious", "playful"}


# -----------------------------
# Helpers
# -----------------------------
def _clip_int(x: Any, lo: int = 0, hi: int = 100, default: int = 60) -> int:
    try:
        v = int(x)
    except Exception:
        return default
    return max(lo, min(hi, v))


def _safe_str(x: Any) -> str:
    return (str(x) if x is not None else "").strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    """
    Robust JSON extractor:
    - If the model returns extra text, grab the first {...} block.
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("empty model output")

    # direct parse first
    try:
        return json.loads(t)
    except Exception:
        pass

    # try to find first JSON object
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        raise ValueError("no json object found")
    return json.loads(m.group(0))


def build_sentiment_context(history_rows: List[Any], max_turns: int = 12) -> str:
    """
    Build a compact transcript for mood interpretation.
    Includes both guest + assistant; last turns only.
    """
    rows = history_rows[-max_turns:] if len(history_rows) > max_turns else history_rows
    lines: List[str] = []
    for m in rows:
        sender = (_safe_str(getattr(m, "sender", "guest")).lower() or "guest")
        role = "assistant" if sender == "assistant" else "guest"
        content = _safe_str(getattr(m, "content", ""))

        if not content:
            continue

        # cap per line to avoid huge prompts
        content = content.replace("\n", " ").strip()[:280]
        lines.append(f"{role}: {content}")

    return "\n".join(lines)


def sentiment_fallback_rule(current_text: str) -> Tuple[str, str, int, Dict[str, Any]]:
    """
    Deterministic fallback when OpenAI fails.
    """
    t = (current_text or "").lower()

    # hard reset / de-escalate
    if _RESET_RE.search(t) or _PLAYFUL_RE.search(t):
        return ("neutral", "calm", 90, {"reset": True})

    if _ESCALATE_RE.search(t):
        return ("negative", "angry", 85, {"escalation": True})

    if any(w in t for w in NEGATIVE_WORDS):
        # confusion should map to "confused" rather than "upset" sometimes
        if "confus" in t or "doesn't make sense" in t or "what do you mean" in t:
            return ("negative", "confused", 70, {})
        return ("negative", "upset", 70, {})

    if any(w in t for w in POSITIVE_WORDS):
        return ("positive", "happy", 70, {})

    return ("neutral", "calm", 60, {})


# -----------------------------
# Main classifier (OpenAI first)
# -----------------------------
def classify_guest_sentiment(client, history_rows: List[Any], current_text: str) -> Dict[str, Any]:
    """
    Returns:
      {
        "sentiment": "positive|neutral|negative",
        "mood": "...",
        "confidence": 0-100,
        "source": "openai|override|fallback",
        "flags": {...}
      }

    IMPORTANT:
      - Save ONLY the string to ChatMessage.sentiment (your DB column is String).
      - Keep the rest for UI / analytics until you add JSONB storage.
    """
    cur = _safe_str(current_text)

    # 0) Fast deterministic overrides (super important for jokes)
    if _RESET_RE.search(cur) or _PLAYFUL_RE.search(cur):
        return {
            "sentiment": "neutral",
            "mood": "playful" if _PLAYFUL_RE.search(cur) else "calm",
            "confidence": 92,
            "source": "override",
            "flags": {"reset": True, "playful": bool(_PLAYFUL_RE.search(cur))},
        }

    if _ESCALATE_RE.search(cur):
        return {
            "sentiment": "negative",
            "mood": "angry",
            "confidence": 88,
            "source": "override",
            "flags": {"escalation": True},
        }

    # 1) Build context
    ctx = build_sentiment_context(history_rows, max_turns=12)

    # 2) OpenAI prompt (context-aware, “current message” focus)
    prompt = f"""
You are labeling the GUEST'S CURRENT message in a hospitality chat.

Use the context ONLY to interpret the CURRENT message.
If the guest is joking, teasing, or clearly playful, do NOT label them upset/angry.

Return ONLY valid JSON:
{{
  "sentiment": "positive" | "neutral" | "negative",
  "mood": "happy" | "calm" | "playful" | "confused" | "upset" | "angry" | "anxious",
  "confidence": 0-100,
  "flags": {{
    "joking": true|false,
    "resolved": true|false,
    "escalation": true|false
  }}
}}

Context (recent turns):
{ctx}

Guest CURRENT message:
{cur}
""".strip()

    model = (os.getenv("OPENAI_SENTIMENT_MODEL") or os.getenv("OPENAI_CHAT_MODEL") or "gpt-4o-mini").strip()

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": "Return strict JSON only. No markdown, no extra text."},
                {"role": "user", "content": prompt},
            ],
        )

        raw = _safe_str(resp.choices[0].message.content)
        data = _extract_json_object(raw)

        sentiment = _safe_str(data.get("sentiment")).lower() or "neutral"
        mood = _safe_str(data.get("mood")).lower() or "calm"
        confidence = _clip_int(data.get("confidence"), default=60)

        flags = data.get("flags") if isinstance(data.get("flags"), dict) else {}
        flags = {k: bool(v) for k, v in flags.items()}

        if sentiment not in {"positive", "neutral", "negative"}:
            sentiment = "neutral"
        if mood not in MOODS:
            mood = "calm"

        # 3) Post-adjustments (reactionary improvements)
        # If the guest is flagged joking OR contains playful markers, do not keep "upset/angry"
        if flags.get("joking") or _PLAYFUL_RE.search(cur):
            if sentiment == "negative" and confidence < 90:
                sentiment = "neutral"
            if mood in {"upset", "angry", "anxious"}:
                mood = "playful" if _PLAYFUL_RE.search(cur) else "calm"
            flags["playful_override"] = True

        # If the guest seems to be resolving after a negative context
        if flags.get("resolved") or _RESET_RE.search(cur):
            if sentiment == "negative" and confidence < 95:
                sentiment = "neutral"
            if mood in {"upset", "angry"}:
                mood = "calm"
            flags["resolved_override"] = True

        return {
            "sentiment": sentiment,
            "mood": mood,
            "confidence": confidence,
            "source": "openai",
            "flags": flags,
        }

    except Exception:
        s, m, c, flags = sentiment_fallback_rule(cur)
        return {
            "sentiment": s,
            "mood": m,
            "confidence": c,
            "source": "fallback",
            "flags": flags or {},
        }
