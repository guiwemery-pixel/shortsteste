"""Nomes de arquivo para download a partir dos títulos sugeridos."""

from __future__ import annotations

import re
import unicodedata

# Caracteres proibidos em nomes de arquivo no Windows (e barras em qualquer sistema).
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME_LENGTH = 120


def safe_filename(title: str, fallback: str) -> str:
    """Título do corte como nome de arquivo válido no Windows, Mac e Linux (sem extensão)."""
    name = unicodedata.normalize("NFC", title or "")
    name = _INVALID.sub(" ", name)
    name = " ".join(name.split())
    if len(name) > MAX_NAME_LENGTH:
        name = name[:MAX_NAME_LENGTH].rsplit(" ", 1)[0] or name[:MAX_NAME_LENGTH]
    name = name.rstrip(" .")
    if not name or name.split(".")[0].upper() in _RESERVED:
        return fallback
    return name


def unique_names(names: list[str]) -> list[str]:
    """Evita nomes repetidos (ex.: dois cortes com o mesmo título) acrescentando (2), (3)…"""
    seen: dict[str, int] = {}
    result = []
    for name in names:
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        result.append(name if seen[key] == 1 else f"{name} ({seen[key]})")
    return result
