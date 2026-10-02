"""Extract an explicitly stated multiple-choice letter from a model solution."""

import re


_ANSWER_LETTER = re.compile(
    r"\bthe\s+answer\s+is\s*[:\-]?\s*\**\s*\(?([A-E])\)?"
    r"(?=\s|[.)*:,]|$)",
    re.IGNORECASE,
)


def extract_answer_choice(solution, choices):
    """Return a supplied choice, or None when no valid final letter was stated."""
    if not solution or not choices:
        return None
    valid_letters = "ABCDE"[:len(choices)]
    matches = [letter.upper() for letter in _ANSWER_LETTER.findall(solution)]
    for letter in reversed(matches):
        if letter in valid_letters:
            return choices[valid_letters.index(letter)]
    return None
