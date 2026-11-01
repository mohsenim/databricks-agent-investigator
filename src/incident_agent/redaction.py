"""The one place that hides sensitive text. Every string that can reach a prompt goes
through here.

It is best effort. The stronger protection is that no tool reads table rows.
"""
import re

from .config import Config

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     "<private-key>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "<jwt>"),
    (re.compile(r"\bdapi[0-9a-f]{20,}\b"), "<token>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<aws-key>"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\b(\s*[=:]\s*)\S+"),
     r"\1\2<secret>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"\bhttps?://[^\s'\"]*\?[^\s'\"]*"), "<url-with-query>"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}"), "<long-token>"),
    (re.compile(r"(?<!\d)\d{6,}(?!\d)"), "<num>"),
]
# Quoted strings that are longer than a short identifier.
_LITERAL = re.compile(r"(['\"])((?:(?!\1)[^\\\n]|\\.){4,})\1")
_IDENT_LIKE = re.compile(r"^[A-Za-z_][\w.]*$")  # column and table names stay readable


def _mask_literal(m: re.Match[str]) -> str:
    inner = m.group(2)
    if _IDENT_LIKE.match(inner) and len(inner) <= 64:
        return m.group(0)
    return f"{m.group(1)}<literal>{m.group(1)}"


def redact(text: str, max_chars: int | None = None, mask_literals: bool = True) -> str:
    """Hide sensitive patterns, then cut the text and keep the end (the cause is usually
    at the end).
    """
    limit = max_chars if max_chars is not None else Config().max_text_chars
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    if mask_literals:
        text = _LITERAL.sub(_mask_literal, text)
    if len(text) > limit:
        text = "...[truncated]\n" + text[-limit:]
    return text
