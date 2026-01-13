import os
import json
import re

# Strong “reset” patterns
_JOKE_RESET_RE = re.compile(r"\b(jk|j/k|just kidding|kidding|i was joking|only joking|never mind|nevermind)\b", re.I)

def sentiment_fallback_rule(text: str):
    t = (text or "").lower()

    # hard reset
    if _JOKE_RESET_RE.search(t):
        return ("neutral", "calm", 95, {"reset": True})

    negative = ["terrible", "awful", "angry", "bad", "disappointed", "upset", "mad", "furious"]
    positive = ["great", "amazing", "awesome", "love", "fantastic", "perfect", "thank you", "thanks"]

    if any(w in t for w in negative):
        return ("negative", "upset", 70, {})
    if any(w in t for w in positive):
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


def classify_guest_sentiment(client, history_rows, current_text: str):
    # 1) deterministic override
    if _JOKE_RESET_RE.search(current_text or ""):
        return {
            "sentiment": "neutral",
            "mood": "calm",
            "confidence": 95,
            "source": "override",
            "flags": {"reset": True},
        }

    ctx = build_sentiment_context(history_rows, max_turns=10)

    prompt = f"""
Label the GUEST'S CURRENT message in a hospitality chat.
Use the context transcript only to interpret the current message.

Return ONLY valid JSON with:
{{
  "sentiment": "positive"|"neutral"|"negative",
  "mood": "happy"|"calm"|"confused"|"upset"|"angry"|"anxious",
  "confidence": 0-100
}}

Context:
{ctx}

Guest CURRENT message:
{current_text}
""".strip()

    model = (os.getenv("OPENAI_SENTIMENT_MODEL") or os.getenv("OPENAI_CHAT_MODEL") or "gpt-4o-mini").strip()

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": "Return strict JSON only. No markdown."},
                {"role": "user", "content": prompt},
            ],
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        sentiment = str(data.get("sentiment") or "neutral").lower().strip()
        mood = str(data.get("mood") or "calm").lower().strip()
        confidence = int(data.get("confidence") or 60)

        if sentiment not in {"positive", "neutral", "negative"}:
            sentiment = "neutral"
        if mood not in {"happy","calm","confused","upset","angry","anxious"}:
            mood = "calm"
        confidence = max(0, min(100, confidence))

        return {
            "sentiment": sentiment,
            "mood": mood,
            "confidence": confidence,
            "source": "openai",
            "flags": {},
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
