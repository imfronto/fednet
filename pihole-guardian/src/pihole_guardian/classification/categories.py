"""Classification categories, per requirement #3. Ordered roughly from most
to least "block-eligible" purely for readability — the decision engine does
not rely on this ordering."""
from __future__ import annotations

import enum


class Category(str, enum.Enum):
    KNOWN_MALICIOUS = "known_malicious"
    STRONGLY_SUSPICIOUS = "strongly_suspicious"
    ADVERTISING_TRACKING = "advertising_tracking"
    TELEMETRY_ANALYTICS = "telemetry_analytics"
    POTENTIALLY_UNWANTED = "potentially_unwanted"
    UNKNOWN = "unknown"
    LEGITIMATE_INFRASTRUCTURE = "legitimate_infrastructure"
    KNOWN_LEGITIMATE = "known_legitimate"


NEGATIVE_CATEGORIES = {
    Category.KNOWN_MALICIOUS,
    Category.STRONGLY_SUSPICIOUS,
    Category.ADVERTISING_TRACKING,
    Category.TELEMETRY_ANALYTICS,
    Category.POTENTIALLY_UNWANTED,
}

PROTECTIVE_CATEGORIES = {Category.LEGITIMATE_INFRASTRUCTURE, Category.KNOWN_LEGITIMATE}
