"""Small shared title guard used by the extracted V4 candidate adapter."""
from __future__ import annotations

import re
import unicodedata


def _non_title_text(text: str | None) -> bool:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"\s+", " ", value).strip().casefold()
    number = r"\d[\d., ]*"
    money = r"(?:[€$£]\s*" + number + r"|" + number + r"\s*(?:€|eur|usd|gbp|\$|£))"
    return bool(
        re.fullmatch(r"(?:(?:\d+\s+ofertas?\s+desde|desde|from|a partir de|precio:?)[ :]*\s*)?"
                     + money + r"(?:\s*[-–]\s*" + money + r")?", value)
        or re.fullmatch(r"[\d.,]+\s*(?:de|out of)\s*5\s*(?:estrellas|stars)"
                        r"(?:[, ]+[\d.,]+\s*(?:calificaciones|ratings))?", value)
        or re.fullmatch(r"[\d.,]+\s*(?:calificaciones|ratings|valoraciones|resenas|reviews)", value)
    )
