# ----------------------------
# renders sections + dropdowns + lists CONFIG.JSON
# ----------------------------
CONFIG_FORM = [
    {
        "section": "Assistant",
        "description": "Brand voice + behavior settings for Sandy.",
        "fields": [
            {"path": "assistant.name", "label": "Assistant name", "type": "text", "default": "Sandy"},
            {"path": "assistant.tone", "label": "Tone", "type": "select", "default": "luxury",
             "options": [("luxury","Luxury"),("beachy","Beachy"),("professional","Professional"),("friendly","Friendly")]},
            {"path": "assistant.verbosity", "label": "Verbosity", "type": "select", "default": "balanced",
             "options": [("very_short","Very short"),("balanced","Balanced"),("detailed","Detailed")]},
            {"path": "assistant.emoji_level", "label": "Emoji level", "type": "select", "default": "light",
             "options": [("none","None"),("light","Light"),("medium","Medium"),("high","High")]},
            {"path": "assistant.formality", "label": "Formality", "type": "select", "default": "polished",
             "options": [("casual","Casual"),("friendly","Friendly"),("polished","Polished"),("formal","Formal")]},
            {"path": "assistant.avatar_url", "label": "Avatar URL", "type": "text", "default": "/static/img/sandy.png"},
            {"path": "assistant.style", "label": "Style (1–2 sentences)", "type": "textarea", "default": ""},
            {"path": "assistant.extra_instructions", "label": "Extra instructions", "type": "textarea", "default": ""}
        ]
    },
    {
        "section": "Assistant Guidelines",
        "description": "Short bullet rules Sandy should follow/avoid.",
        "fields": [
            {"path": "assistant.do", "label": "Do (one per line)", "type": "list_text", "default": []},
            {"path": "assistant.dont", "label": "Don’t (one per line)", "type": "list_text", "default": []}
        ]
    },
    {
        "section": "Voice & Templates",
        "description": "Guest-facing messages. Supports {{guest_name}}, {{assistant_name}}, {{property_name}}.",
        "fields": [
            {"path": "assistant.voice.welcome_template", "label": "Welcome template (with guest name)", "type": "textarea", "default": ""},
            {"path": "assistant.voice.welcome_template_no_name", "label": "Welcome template (no guest name)", "type": "textarea", "default": ""},
            {"path": "assistant.voice.offline_message", "label": "Offline message", "type": "textarea", "default": ""},
            {"path": "assistant.voice.fallback_message", "label": "Fallback message", "type": "textarea", "default": ""},
            {"path": "assistant.voice.error_message", "label": "Error message", "type": "textarea", "default": ""}
        ]
    },
    {
        "section": "Quick Replies",
        "description": "Buttons guests can tap. One per line.",
        "fields": [
            {"path": "assistant.quick_replies", "label": "Quick replies", "type": "list_text", "default": []}
        ]
    },
    {
        "section": "Property Info",
        "description": "Shown to the assistant and used for templates.",
        "fields": [
            {"path": "property.display_name", "label": "Property display name", "type": "text", "default": ""},
            {"path": "property.address_line", "label": "Address line (optional)", "type": "text", "default": ""},
            {"path": "property.timezone", "label": "Timezone", "type": "select", "default": "America/Los_Angeles",
             "options": [
                 ("America/Los_Angeles","America/Los_Angeles"),
                 ("America/Denver","America/Denver"),
                 ("America/Chicago","America/Chicago"),
                 ("America/New_York","America/New_York")
             ]},
            {"path": "property.check_in_time", "label": "Check-in time", "type": "text", "default": "4:00 PM"},
            {"path": "property.check_out_time", "label": "Check-out time", "type": "text", "default": "10:00 AM"}
        ]
    },
    {
        "section": "Quiet Hours",
        "description": "Used for noise/house rules responses.",
        "fields": [
            {"path": "property.quiet_hours.enabled", "label": "Enable quiet hours", "type": "toggle", "default": False},
            {"path": "property.quiet_hours.start", "label": "Quiet hours start", "type": "text", "default": "10:00 PM"},
            {"path": "property.quiet_hours.end", "label": "Quiet hours end", "type": "text", "default": "8:00 AM"}
        ]
    },
    {
        "section": "Links",
        "description": "Optional URLs used in answers.",
        "fields": [
            {"path": "links.house_manual_url", "label": "House manual URL", "type": "text", "default": ""},
            {"path": "links.wifi_instructions_url", "label": "WiFi instructions URL", "type": "text", "default": ""},
            {"path": "links.parking_instructions_url", "label": "Parking instructions URL", "type": "text", "default": ""},
            {"path": "links.checkin_instructions_url", "label": "Check-in instructions URL", "type": "text", "default": ""}
        ]
    },
    {
        "section": "Features",
        "description": "Toggle assistant features.",
        "fields": [
            {"path": "features.include_google_maps_links", "label": "Include Google Maps links", "type": "toggle", "default": True},
            {"path": "features.enable_quick_replies", "label": "Enable quick replies", "type": "toggle", "default": True},
            {"path": "features.enable_upsells", "label": "Enable upsells", "type": "toggle", "default": False}
        ]
    },
    {
        "section": "Routing & Handoff",
        "description": "Where the assistant escalates or routes internal tasks.",
        "fields": [
            {"path": "routing.handoff_email", "label": "Handoff email", "type": "text", "default": ""},
            {"path": "routing.auto_assign", "label": "Auto-assign", "type": "select", "default": "off",
             "options": [("off","Off"),("by_role","By role")]},
            {"path": "routing.auto_assign_role", "label": "Auto-assign role", "type": "select", "default": "ops_manager",
             "options": [("owner","Owner"),("admin","Admin"),("ops_manager","Ops manager"),("maintenance","Maintenance"),("cleaner","Cleaner"),("staff","Staff"),("read_only","Read only")]}
        ]
    },
    {
        "section": "Escalation Thresholds",
        "description": "Heat score thresholds (0–100). Must satisfy low < medium < high.",
        "fields": [
            {"path": "escalation.enabled", "label": "Enable escalation", "type": "toggle", "default": True},
            {"path": "escalation.low", "label": "Low threshold", "type": "number", "default": 35, "min": 0, "max": 100},
            {"path": "escalation.medium", "label": "Medium threshold", "type": "number", "default": 60, "min": 0, "max": 100},
            {"path": "escalation.high", "label": "High threshold", "type": "number", "default": 85, "min": 0, "max": 100}
        ]
    }
]

