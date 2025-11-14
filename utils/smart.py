def classify_category(message: str) -> str:
    msg = message.lower()

    if any(term in msg for term in ["wifi", "internet", "connection"]):
        return "wifi"
    if any(term in msg for term in ["tv", "remote", "netflix", "streaming"]):
        return "tv"
    if any(term in msg for term in ["fridge", "grocery", "stock", "food"]):
        return "fridge request"
    if any(term in msg for term in ["checkin", "early", "arrival"]):
        return "check-in"
    if any(term in msg for term in ["clean", "housekeeping", "maid"]):
        return "cleaning"
    if any(term in msg for term in ["broken", "leak", "issue", "not working"]):
        return "maintenance"
    if any(term in msg for term in ["emergency", "urgent", "help"]):
        return "urgent"
    
    return "general"

def smart_response(category: str, emergency_phone: str) -> str:
    replies = {
        "wifi": "Try restarting the router. If that doesn’t work, I’ll notify the host for you.",
        "tv": "Please check if the remote has batteries and the TV is set to HDMI. Need help? I’ll alert the host.",
        "fridge request": "Got it — do you want me to pass this to the host to stock the fridge for you?",
        "check-in": "Let me check with the host if early check-in is available. I’ll get back to you shortly.",
        "cleaning": "Noted! I’ll forward your request to the host right away.",
        "maintenance": "Thanks for letting us know. I’ll alert the host and get someone to assist.",
        "urgent": f"If this is an emergency, please call {emergency_phone}. I’m also alerting the host.",
        "general": "Thanks for your message! I’ll pass this along to the host and reply soon."
    }

    return replies.get(category, replies["general"])

def detect_log_types(message: str) -> str:
    msg = message.lower()

    if "fridge" in msg or "stock" in msg:
        return "Fridge Request"
    if "checkin" in msg or "early" in msg:
        return "Early Access"
    if "clean" in msg:
        return "Cleaning"
    if "broken" in msg or "maintenance" in msg:
        return "Maintenance"
    if "emergency" in msg or "urgent" in msg:
        return "Urgent"

    return "General"
