import os
import json
import re
from typing import Any, Dict, List, Tuple

# ----------------------------
# Pattern helpers
# ----------------------------

# Strong explicit reset patterns
_JOKE_RESET_RE = re.compile(
    r"\b(jk|j/k|just kidding|kidding|i was joking|only joking|never mind|nevermind)\b",
    re.I,
)

# Playful / teasing markers (covers "lol", "lmao", emojis, "bro", "😂", etc.)
_PLAYFUL_RE = re.compile(
    r"(\blol\b|\blmao\b|\brofl\b|\bhaha\b|\bhehe\b|😂|🤣|😅|😉|😜|😆|😁|😄|😭|"
    r"\bjust playing\b|\bplaying\b|\bteasing\b|\bi'm kidding\b|\bim kidding\b|\bdead\b|\bbro\b)",
    re.I,
)

# Sarcasm cues
_SARCASM_RE = re.compile(
    r"(\byeah right\b|\bsure\b.*\bnot\b|\bas if\b|\bokay then\b|\bnice\b\W*$|\bgreat\b\W*$)",
    re.I,
)

# Strong negative cues
_NEG_STRONG_RE = re.compile(r"\b(furious|livid|unacceptable|ruined|scam|worst)\b", re.I)

# Mild negative cues
_NEG_MILD_RE = re.compile(r"\b(upset|mad|angry|annoyed|disappointed|bad|terrible|awful)\b", re.I)

# Positive cues
_POS_RE = re.compile(r"\b(thanks|thank you|great|amazing|awesome|love|fantastic|perfect)\b", re.I)


def sentiment_fallback_rule(text: str) -> Tuple[str, str, int, Dict[str, Any]]:
    """
    Deterministic fallback.
    Returns: (sentiment, mood, confidence, flags)
    """
    t = (text or "").strip()
    tl = t.lower()

    # hard reset
    if _JOKE_RESET_RE.search(tl):
        return ("neutral", "calm", 95, {"reset": True})

    # playful override (prevents "confused/upset" on jokes)
    if _PLAYFUL_RE.search(tl) and not _NEG_STRONG_RE.search(tl):
        return ("neutral", "happy", 80, {"playful": True})

    # sarcasm: usually negative *tone*, but not always "upset"
    if _SARCASM_RE.search(tl) and _NEG_MILD_RE.search(tl):
        return ("negative", "annoyed" if "annoyed" in {"annoyed"} else "upset", 70, {"sarcasm": True})

    if _NEG_STRONG_RE.search(tl):
        return ("negative", "angry", 85, {"strong_negative": True})

    if _NEG_MILD_RE.search(tl):
        return ("negative", "upset", 70, {})

    if _POS_RE.search(tl):
        return ("positive", "happy", 70, {})

    return ("neutral", "calm", 60, {})


def build_sentiment_context(history_rows, max_turns: int = 10) -> str:
    rows = history_rows[-max_turns:] if len(history_rows) > max_turns else history_rows
    lines = []
    for m in rows:
        sender = (getattr(m, "sender", "") or "guest").strip().lower()
        role = "assistant" if sender == "assistant" else "guest"
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:300]}")
    return "\n".join(lines)


def _recent_guest_mood(history_rows, max_turns: int = 6) -> str | None:
    """
    If you store sentiment only as a string today, we can't read mood from DB.
    But we can still infer a simple prior mood signal from prior guest text.
    """
    rows = history_rows[-max_turns:] if len(history_rows) > max_turns else history_rows
    for m in reversed(rows):
        sender = (getattr(m, "sender", "") or "").lower().strip()
        if sender not in {"guest", "user"}:
            continue
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            continue
        # quick heuristic: if prior message looked playful, remember that
        if _PLAYFUL_RE.search(content.lower()):
            return "playful"
        if _NEG_STRONG_RE.search(content.lower()) or _NEG_MILD_RE.search(content.lower()):
            return "negative"
        if _POS_RE.search(content.lower()):
            return "positive"
    return None


def _clamp01(x: int) -> int:
    return max(0, min(100, int(x)))


def classify_guest_sentiment(client, history_rows, current_text: str) -> Dict[str, Any]:
    """
    OpenAI-first sentiment + mood classification using conversation context,
    with deterministic overrides and deterministic fallback.
    """
    current_text = (current_text or "").strip()

    # 1) deterministic override: explicit "jk"/reset
    if _JOKE_RESET_RE.search(current_text.lower()):
        return {
            "sentiment": "neutral",
            "mood": "calm",
            "confidence": 95,
            "source": "override",
            "flags": {"reset": True},
        }

    # 2) deterministic override: playful markers (unless strong negative)
    if _PLAYFUL_RE.search(current_text.lower()) and not _NEG_STRONG_RE.search(current_text.lower()):
        return {
            "sentiment": "neutral",
            "mood": "happy",
            "confidence": 85,
            "source": "override",
            "flags": {"playful": True},
        }

    ctx = build_sentiment_context(history_rows, max_turns=10)
    prior_signal = _recent_guest_mood(history_rows, max_turns=6)

    # 3) OpenAI classification (context-aware)
    prompt = f"""
You are labeling the GUEST'S CURRENT message in a hospitality chat.

Rules:
- Use the context ONLY to interpret the CURRENT message.
- If the guest is joking / teasing / playful / sarcastic, do NOT label them as upset/confused unless it's clearly genuine frustration.
- Prefer "calm" or "happy" for playful banter.
- Only use "confused" if the guest is truly asking "what does that mean" / "I don't understand" etc.
- Only use "upset/angry/anxious" when the message shows real distress, complaint, threat, or urgency.

Return ONLY valid JSON:
{{
  "sentiment": "positive"|"neutral"|"negative",
  "mood": "happy"|"calm"|"confused"|"upset"|"angry"|"anxious",
  "confidence": 0-100,
  "playful": true|false
}}

Context transcript:
{ctx}

Prior guest signal (heuristic): {prior_signal or "none"}

Guest CURRENT message:
{current_text}
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

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        sentiment = str(data.get("sentiment") or "neutral").lower().strip()
        mood = str(data.get("mood") or "calm").lower().strip()
        confidence = _clamp01(data.get("confidence") or 60)
        playful = bool(data.get("playful") or False)

        if sentiment not in {"positive", "neutral", "negative"}:
            sentiment = "neutral"
        if mood not in {"happy", "calm", "confused", "upset", "angry", "anxious"}:
            mood = "calm"

        # 4) Light smoothing: if model says upset/confused but message is playful-ish, soften it
        if (mood in {"confused", "upset", "angry"} or sentiment == "negative") and _PLAYFUL_RE.search(current_text.lower()):
            mood = "happy"
            sentiment = "neutral"
            confidence = max(confidence - 10, 55)
            playful = True

        flags: Dict[str, Any] = {}
        if playful:
            flags["playful"] = True
        if prior_signal:
            flags["prior_signal"] = prior_signal

        return {
            "sentiment": sentiment,
            "mood": mood,
            "confidence": confidence,
            "source": "openai",
            "flags": flags,
        }

    except Exception:
        s, m, c, flags = sentiment_fallback_rule(current_text)
        return {
            "sentiment": s,
            "mood": m,
            "confidence": c,
            "source": "fallback",
            "flags": flags or {},
        }
