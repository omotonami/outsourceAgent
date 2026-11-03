"""Mask PII trước khi vào prompt (email, số điện thoại SG, NRIC/FIN)."""
import re

PATTERNS = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "NRIC": re.compile(r"\b[STFGM]\d{7}[A-Z]\b"),
    "PHONE_SG": re.compile(r"(?:\+65[ -]?)?\b[689]\d{3}[ -]?\d{4}\b"),
}


def mask(text: str) -> str:
    for label, pat in PATTERNS.items():
        text = pat.sub(f"[{label}]", text)
    return text


def mask_message(msg: dict) -> dict:
    if isinstance(msg.get("content"), str):
        return {**msg, "content": mask(msg["content"])}
    return msg
