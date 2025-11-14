def classify_category(message: str) -> str:
    triggers = {
        "urgent": ["emergency", "ASAP", "urgent", "leaking", "water everywhere", "flooding", "no power", "locked out", "gas", "fire"],
        "maintenance": ["broken", "not working", "jammed", "stuck", "won’t start", "TV", "AC", "wifi"],
        "cleaning": ["maid", "towels", "linens", "trash", "cleaning"],
        "request": ["Can we get", "Could you bring", "Need more", "Do you have"],
        "extension": ["extend stay", "extra night", "late checkout"],
        "entertainment": ["recommendations", "things to do", "what’s happening", "local events"],
    }
    for category, keywords in triggers.items():
        if any(k.lower() in message.lower() for k in keywords):
            return category
    return "other"

def smart_response(category: str, emergency_phone: str) -> str:
    if category == "urgent":
        return f"Got it — I flagged that as urgent. If it's an emergency, you can also call {emergency_phone} 📞"
    if category == "cleaning":
        return "Thanks! I'll pass that cleaning request along. 🧼"
    if category == "maintenance":
        return "Thanks! I’ll let the host know about that maintenance issue. 🔧"
    if category == "extension":
        return "Happy to help extend your stay — just let me know how many nights! 🏖️"
    if category == "entertainment":
        return "Ooooh fun! I’ve got great local tips. Want tacos, beach bars, or something unique? 🌮"
    return "Thanks for your message! I’ll pass that along to the host. 🌴"

def detect_log_types(message: str) -> str:
    if "fridge" in message.lower() or "stock" in message.lower():
        return "Prearrival Interest"
    if "extend" in message.lower():
        return "Extension"
    return "General"
