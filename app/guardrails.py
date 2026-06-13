"""Guardrail utilities for input filtering and safety checks.

Provides a 4-stage pipeline:
  1. filter_input      — length / empty checks with Unicode normalization
  2. detect_prompt_injection — pattern-based injection detection
  3. detect_policy_violation — keyword-based policy enforcement
  4. moderate_output   — output safety scan
"""

import re
import unicodedata
from typing import Tuple


# ---------------------------------------------------------------------------
# Zero-width / invisible character stripping
# ---------------------------------------------------------------------------

# Characters commonly inserted into keywords to break pattern matching.
_ZERO_WIDTH_CHARS = (
    "​"  # ZERO WIDTH SPACE
    "‌"  # ZERO WIDTH NON-JOINER
    "‍"  # ZERO WIDTH JOINER
    "﻿"  # ZERO WIDTH NO-BREAK SPACE (BOM)
    "­"  # SOFT HYPHEN
    "⁠"  # WORD JOINER
    "⁡"  # FUNCTION APPLICATION
    "⁢"  # INVISIBLE TIMES
    "⁣"  # INVISIBLE SEPARATOR
    "⁤"  # INVISIBLE PLUS
    "᠎"  # MONGOLIAN VOWEL SEPARATOR
)
_ZERO_WIDTH_RE = re.compile(f"[{re.escape(_ZERO_WIDTH_CHARS)}]")

# ---------------------------------------------------------------------------
# Cross-script confusable mapping
# ---------------------------------------------------------------------------
# Cyrillic, Greek, and other script characters that are visually identical to
# ASCII letters are mapped to their ASCII equivalents.  This covers the most
# common homoglyph substitution attack vectors; a full implementation would use
# the Unicode TR39 confusables.txt dataset.
_CONFUSABLES: dict[str, str] = {
    # Cyrillic lookalikes
    "а": "a",  # а → a
    "е": "e",  # е → e
    "і": "i",  # і → i  (Byelorussian-Ukrainian I)
    "о": "o",  # о → o
    "р": "p",  # р → p
    "с": "c",  # с → c
    "х": "x",  # х → x
    "у": "y",  # у → y
    "В": "B",  # В → B
    "Н": "H",  # Н → H
    "І": "I",  # І → I
    "К": "K",  # К → K
    "М": "M",  # М → M
    "О": "O",  # О → O
    "Р": "P",  # Р → P
    "С": "C",  # С → C
    "Т": "T",  # Т → T
    "Х": "X",  # Х → X
    "А": "A",  # А → A
    "Е": "E",  # Е → E
    # Greek lookalikes
    "ο": "o",  # ο → o (Greek small letter omicron)
    "Ο": "O",  # Ο → O (Greek capital letter omicron)
    "ρ": "p",  # ρ → p (Greek small letter rho)
    "ν": "v",  # ν → v (Greek small letter nu)
}
_CONFUSABLES_TABLE = str.maketrans(_CONFUSABLES)

_MAX_INPUT_LENGTH = 1000


# ---------------------------------------------------------------------------
# Core normalization helper
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Normalize text for consistent pattern matching.

    Applies a four-step normalization pipeline:
    1. NFKC Unicode normalization — maps compatibility equivalents (fullwidth
       ASCII, ligatures, etc.) to their canonical Unicode forms.
    2. Cross-script confusable substitution — replaces Cyrillic and Greek
       characters that are visually identical to ASCII letters (e.g. Cyrillic
       small letter i U+0456 -> Latin i) so keyword patterns cannot be bypassed
       by substituting look-alike characters from other scripts.
    3. Zero-width / invisible character removal — strips code points that are
       invisible in most fonts and are used to split keywords without changing
       their visual appearance (e.g. "ign​ore" with U+200B inside).
    4. Whitespace collapse — collapses any run of whitespace (including
       non-breaking space U+00A0, ideographic space U+3000, etc.) to a single
       ASCII space and strips leading/trailing whitespace.

    Args:
        text: Raw input string.

    Returns:
        Normalized string ready for case-insensitive regex matching.
    """
    # Step 1: NFKC normalization
    normalized = unicodedata.normalize("NFKC", text)
    # Step 2: Cross-script confusable substitution
    normalized = normalized.translate(_CONFUSABLES_TABLE)
    # Step 3: Strip zero-width / invisible characters
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    # Step 4: Collapse whitespace (covers U+00A0 non-breaking space, U+3000, tabs, etc.)
    normalized = re.sub(r"[\s 　]+", " ", normalized)
    return normalized.strip()


# ---------------------------------------------------------------------------
# Input filter
# ---------------------------------------------------------------------------


def filter_input(text: str) -> Tuple[bool, str]:
    """Filter and sanitize user input.

    Normalizes, validates length, and rejects empty strings.

    Args:
        text: Raw user input.

    Returns:
        Tuple of (is_valid, filtered_text_or_error_reason).
    """
    if not text or len(text.strip()) == 0:
        return False, "Input cannot be empty"

    if len(text) > _MAX_INPUT_LENGTH:
        return False, f"Input is too long (max {_MAX_INPUT_LENGTH} characters)"

    filtered = normalize_text(text)

    if not filtered:
        return False, "Input cannot be empty"

    return True, filtered


# ---------------------------------------------------------------------------
# Prompt injection detection
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    # Classic "ignore X instructions" family
    r"ignore\s+(previous|above|all|prior)\s+(instructions?|rules?|prompts?|directives?)",
    r"ignore\s+(instructions?|rules?|prompts?|directives?)",
    r"ignore\s+all\s+(previous|above|prior)",
    r"ignore\s+everything",
    # Disregard / forget
    r"disregard\s+(previous|above|all|prior|your)",
    r"forget\s+(your|previous|all|prior)\s+(instructions?|rules?|training|directives?)",
    # Role / identity hijacking
    r"you\s+are\s+now\s+",
    r"pretend\s+you\s+are\s+",
    r"respond\s+only\s+as\s+",
    r"from\s+now\s+on\s+you",
    r"simulate\s+a\s+",
    r"act\s+as\s+a\s+",
    r"stop\s+being\s+",
    # DAN / developer mode jailbreaks
    r"\bdan\b",  # Do Anything Now
    r"developer\s+mode",
    r"jailbreak",
    # Override / bypass
    r"override\s+your\s+",
    r"bypass\s+your\s+",
    r"do\s+not\s+follow\s+",
    # New instruction injection
    r"your\s+new\s+instructions?\s+",
    r"new\s+(role|instructions?|system|directive)",
    # Prompt delimiter tricks
    r"system\s*:+",
    r"\[\s*system\s*\]",
    r"\[\[\s*instructions?\s*\]\]",
    r"<\s*prompt\s*>",
    r"<\s*system\s*>",
]

_COMPILED_INJECTION = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def detect_prompt_injection(text: str) -> Tuple[bool, str]:
    """Detect potential prompt injection attempts.

    Normalizes the input (handling Unicode homoglyphs and zero-width character
    tricks) before running regex matching with explicit IGNORECASE.

    Args:
        text: User-supplied text (already filtered by filter_input is ideal).

    Returns:
        Tuple of (is_injection_detected, reason).
    """
    normalized = normalize_text(text)

    for pattern in _COMPILED_INJECTION:
        if pattern.search(normalized):
            return True, "Prompt injection attempt detected"

    return False, ""


# ---------------------------------------------------------------------------
# Policy violation detection
# ---------------------------------------------------------------------------

# Harmful / illegal content
_HARMFUL_KEYWORDS = [
    "hack", "crack", "steal", "illegal", "drug", "weapon",
    "violence", "harm", "kill", "murder", "suicide", "bomb",
    "exploit", "ransomware", "malware", "phishing",
]

# Personal data exfiltration requests
_PERSONAL_DATA_KEYWORDS = [
    "social security", "ssn", "credit card", "password",
    "personal information", "private data", "confidential",
    "bank account", "routing number", "date of birth",
]

# Off-topic for an e-commerce support agent
_OFF_TOPIC_KEYWORDS = [
    "weather", "sports", "politics", "religion",
    "movie", "recipe", "travel destination", "homework",
    "stock price", "cryptocurrency", "medical advice",
]


def detect_policy_violation(text: str) -> Tuple[bool, str]:
    """Detect policy-violating content in user input.

    Checks for harmful/illegal content, personal data requests, and off-topic
    queries after normalizing the text.

    Args:
        text: User-supplied text.

    Returns:
        Tuple of (is_violation_detected, reason).
    """
    normalized = normalize_text(text).lower()

    for keyword in _HARMFUL_KEYWORDS:
        if keyword in normalized:
            return True, "Policy violation: Harmful/illegal content"

    for keyword in _PERSONAL_DATA_KEYWORDS:
        if keyword in normalized:
            return True, "Policy violation: Personal data request"

    for keyword in _OFF_TOPIC_KEYWORDS:
        if keyword in normalized:
            return True, "Policy violation: Off-topic query"

    return False, ""


# ---------------------------------------------------------------------------
# Output moderation
# ---------------------------------------------------------------------------

_SYSTEM_LEAK_PHRASES = [
    "system prompt",
    "my instructions",
    "i am programmed",
    "as an ai model",
    "my training",
    "i was instructed",
    "my directives",
    "my guidelines say",
    "my configuration",
]

_OUTPUT_HARMFUL_PATTERNS = [
    re.compile(r"\b(password|credit\s*card|ssn)\b.*?[:=]", re.IGNORECASE),
    re.compile(r"\d{3}-\d{2}-\d{4}"),                          # SSN format
    re.compile(r"\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}"),  # Credit card format
]

# Detects multi-line "I am <persona>" roleplay patterns — a sign the model
# has been successfully jailbroken into playing a different character.
_ROLEPLAY_PERSONA_RE = re.compile(
    r"^i\s+am\s+(?!a\s+customer\s+service|here\s+to\s+help|unable|sorry|afraid|not)",
    re.IGNORECASE | re.MULTILINE,
)


def moderate_output(text: str) -> Tuple[bool, str]:
    """Moderate LLM output for safety issues.

    Normalizes the output before scanning for system prompt leaks, sensitive
    data patterns, and roleplay persona injections.

    Args:
        text: Raw LLM response text.

    Returns:
        Tuple of (is_safe, reason_if_unsafe).
    """
    normalized = normalize_text(text)
    normalized_lower = normalized.lower()

    # Check for system information leakage
    for phrase in _SYSTEM_LEAK_PHRASES:
        if phrase in normalized_lower:
            return False, "Output contains system information leak"

    # Check for sensitive data patterns
    for pattern in _OUTPUT_HARMFUL_PATTERNS:
        if pattern.search(normalized):
            return False, "Output contains sensitive information"

    # Check for multi-line roleplay persona (model identity hijack)
    matches = _ROLEPLAY_PERSONA_RE.findall(normalized)
    if len(matches) >= 2:
        return False, "Output contains roleplay persona pattern"

    return True, ""
