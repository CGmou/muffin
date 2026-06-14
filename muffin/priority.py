"""Human-friendly job priority levels.

The manager stores priority as a plain number (higher runs first); the apps and
submitters present it as one of five named levels so artists pick "Normal" or
"Urgent" instead of guessing a magic number. The levels are 20% bands across
0..100, with 50 == Normal.

Keep these in sync with the standalone DCC submitters under integrations/ — they
run inside Blender/Maya and can't import this module, so they carry their own
copy of the same list.
"""

# (label, numeric value) — evenly spaced 20% bands; the value is each band's mid.
LEVELS = [
    ("Low", 10),
    ("Below Normal", 30),
    ("Normal", 50),
    ("High", 70),
    ("Urgent", 90),
]
LABELS = [name for name, _ in LEVELS]
DEFAULT_LABEL = "Normal"
_VALUE_BY_LABEL = {name: val for name, val in LEVELS}


def label_for(value) -> str:
    """Bucket a numeric priority into its level name (20% bands; 50 == Normal)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LABEL
    if n < 20:
        return "Low"
    if n < 40:
        return "Below Normal"
    if n < 60:
        return "Normal"
    if n < 80:
        return "High"
    return "Urgent"


def value_for(label: str) -> int:
    """Numeric priority for a level name (defaults to Normal == 50)."""
    return _VALUE_BY_LABEL.get(label, 50)
