"""License plate validation & OCR character correction for Vietnam plates."""
from __future__ import annotations

import re

# Common OCR confusions on Vietnamese plates.
_CHAR_FIX_DIGIT_SLOT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                        "Z": "2", "S": "5", "B": "8", "T": "7", "A": "4"}
_CHAR_FIX_LETTER_SLOT = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"}

# Pattern: 2 digits + 1 letter (+ optional digit) + 4 or 5 digits.
# Covers car (30A-12345), motorcycle (29-H1 12345), etc. Simplified.
_PLATE_REGEX = re.compile(r"^\d{2}[A-Z]\d?-?\d{4,5}$")


def _clean(raw: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (raw or "").upper())


def _fix_by_position(text: str) -> str:
    """Heuristic position-based correction.

    Layout assumed: [d][d][L][?d][d d d d (d)]
    - slot 0,1: digit
    - slot 2: letter
    - slot 3: digit OR letter (series, sometimes 2 letters); keep as-is if letter
    - remaining: digit
    """
    if not text:
        return text
    chars = list(text)
    n = len(chars)
    for i, ch in enumerate(chars):
        if i in (0, 1):
            chars[i] = _CHAR_FIX_DIGIT_SLOT.get(ch, ch)
        elif i == 2:
            chars[i] = _CHAR_FIX_LETTER_SLOT.get(ch, ch)
        elif i == 3:
            # Series slot can be either digit or letter; only auto-fix obvious garbage.
            chars[i] = ch
        else:
            # Remainder must be digits.
            chars[i] = _CHAR_FIX_DIGIT_SLOT.get(ch, ch)
        if i >= n - 1:
            break
    return "".join(chars)


def normalize_plate(raw: str) -> str:
    """Clean + heuristic-fix a raw OCR string."""
    return _fix_by_position(_clean(raw))


def is_valid_plate(text: str) -> bool:
    if not text:
        return False
    return bool(_PLATE_REGEX.match(text))


def format_with_dash(text: str) -> str:
    """Insert dash between series and number, e.g. 30A12345 -> 30A-12345."""
    if not text:
        return text
    m = re.match(r"^(\d{2}[A-Z]\d?)(\d{4,5})$", text)
    if not m:
        return text
    return f"{m.group(1)}-{m.group(2)}"
