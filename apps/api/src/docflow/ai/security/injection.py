"""Prompt-injection detection and disclosure.

This is layer 5 of the defence in docs/ARCHITECTURE.md §7, and deliberately not
the load-bearing one. Capability isolation is: the extraction call has no tools,
no network and no database, so a successful injection produces wrong data in one
workspace and never an action.

What this module adds is *disclosure*. When a document contains text aimed at an
AI system, the user sees a banner saying so. That converts an attack into a trust
signal, and it is cheap.

It is a heuristic and it is stated as one. It will miss novel phrasings and it
will occasionally fire on a document legitimately discussing AI safety. Both are
acceptable because the consequence is a warning banner, not a block.
"""

from __future__ import annotations

import re
import unicodedata

FLAG_INJECTION = "injection_suspected"
FLAG_HIDDEN_TEXT = "hidden_text"
FLAG_EXTERNAL_URLS = "external_urls"

# Phrases that only appear when someone is addressing a language model.
_INSTRUCTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+)?(previous|prior|above|the)\s+\w+",
    r"forget\s+(everything|all|your)\s+\w+",
    r"(reveal|show|print|output|repeat)\s+(your|the)\s+(system\s+)?(prompt|instructions?)",
    r"you\s+are\s+now\s+(a|an|in)\s+\w+",
    r"new\s+(instructions?|system\s+prompt|rules?)\s*:",
    r"</?\s*(system|assistant|instruction)\s*>",
    r"\[\s*(system|admin|override)\s*\]",
    r"do\s+not\s+(mention|tell|report|flag)\s+(this|that|the\s+user)",
    r"mark\s+(all\s+)?(this|these|the)\s+\w*\s*as\s+(verified|confirmed|approved|safe)",
    r"set\s+confidence\s+to\s+(1|100|high|maximum)",
    r"(act|behave|respond)\s+as\s+(if|though|a)\b",
    r"prompt\s+injection",
    r"jailbreak",
    r"DAN\s+mode",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INSTRUCTION_PATTERNS]

# Zero-width and bidi-control characters. Legitimate in some scripts, but a run
# of them inside a Latin/Bengali document is a hiding place, not typography.
_INVISIBLE_RUN = re.compile(r"[​‌‍⁠﻿‪-‮⁦-⁩]{4,}")

_URL = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# Hosts that only appear in a document when something is trying to phone home.
_SUSPICIOUS_HOSTS = re.compile(
    r"(169\.254\.169\.254|metadata\.google|localhost|127\.0\.0\.1|0\.0\.0\.0"
    r"|\b10\.\d+\.\d+\.\d+|\b192\.168\.\d+\.\d+|\b172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)",
    re.IGNORECASE,
)


def scan_for_injection(text: str) -> list[str]:
    """Return security flags for a document's extracted text."""
    flags: list[str] = []
    if not text:
        return flags

    # Scan the raw text, then — unconditionally — scan again with format
    # characters removed. A single zero-width space between two letters
    # ("i​gnore") defeats the raw pass without producing a *run* of
    # invisible characters, so gating the second pass on a run misses it.
    if any(pattern.search(text) for pattern in _COMPILED) or _has_hidden_instruction(text):
        flags.append(FLAG_INJECTION)

    if _INVISIBLE_RUN.search(text) or _uses_interleaved_invisibles(text):
        flags.append(FLAG_HIDDEN_TEXT)

    if _URL.search(text):
        flags.append(FLAG_EXTERNAL_URLS)

    return flags


def _strip_format_chars(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _has_hidden_instruction(text: str) -> bool:
    """Whether removing invisible characters reveals an instruction."""
    stripped = _strip_format_chars(text)
    if stripped == text:
        return False
    return any(pattern.search(stripped) for pattern in _COMPILED)


def _uses_interleaved_invisibles(text: str, *, threshold: int = 3) -> bool:
    """Detect zero-width characters sprinkled between ordinary letters.

    Real text uses ZWJ/ZWNJ adjacent to script characters that need them (Bengali
    conjuncts, emoji sequences). Repeatedly placing them *between Latin letters*
    is obfuscation, not typography.
    """
    hits = 0
    for i, ch in enumerate(text):
        if unicodedata.category(ch) != "Cf":
            continue
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        if before.isascii() and before.isalpha() and after.isascii() and after.isalpha():
            hits += 1
            if hits >= threshold:
                return True
    return False


def extract_urls(text: str) -> list[str]:
    """URLs found in a document.

    These are shown to the user as bare domains behind an interstitial and are
    **never fetched server-side** — that is the SSRF control (T11).
    """
    seen: list[str] = []
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(".,;:)")
        if url not in seen:
            seen.append(url)
    return seen


def is_suspicious_url(url: str) -> bool:
    """Whether a URL targets link-local, loopback or private address space."""
    return bool(_SUSPICIOUS_HOSTS.search(url))


def redact_for_display(text: str, *, max_length: int = 400) -> str:
    """Flatten a quote for safe rendering.

    Invisible characters removed and newlines collapsed, so a quote cannot smuggle
    layout or hidden content into the UI. React escapes HTML already; this handles
    what escaping does not.
    """
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_length]
