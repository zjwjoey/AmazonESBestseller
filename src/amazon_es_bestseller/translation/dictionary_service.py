# -*- coding: utf-8 -*-
"""Offline, field-aware Spanish → Chinese dictionary service.

This module is deliberately conservative.  It only resolves exact or
normalized values that are present in the checked-in high-confidence
dictionaries.  Unknown text is returned as ``unresolved``; it is never
silently erased or sent to a translation provider.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


PACKAGE_DICTIONARIES = Path(__file__).with_name("dictionaries")


# Attribute labels whose values are product identity or legal-entity evidence.
# These values must be copied verbatim; only the label is translated.
_IDENTITY_ATTRIBUTE_KEYS = frozenset({
    "marca", "fabricante", "modelo", "nombre del modelo", "nombre modelo",
    "numero de modelo", "numero modelo", "numero de modelo del producto",
    "referencia", "referencia oem", "referencia del fabricante",
    "numero pieza", "numero de pieza", "numero de pieza del fabricante",
    "part number", "oem", "upc", "ean", "asin", "isbn", "gtin",
    "numero de identificacion comercial global",
})


def normalize_key(value: Any) -> str:
    """Normalize only for lookup; never use this value for display output."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)


def is_identity_attribute(label: Any) -> bool:
    """Return whether a detail label identifies a product/entity value.

    Normalization is lookup-only: source labels and values remain unchanged.
    The matcher accepts common Spanish/English label variants while avoiding
    broad substring matches that could classify ordinary prose as identity.
    """
    key = normalize_key(label).replace("_", " ")
    key = re.sub(r"\bnum\.", "numero", key)
    key = re.sub(r"\bnum\b", "numero", key)
    key = re.sub(r"[-/]+", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    if key in _IDENTITY_ATTRIBUTE_KEYS:
        return True
    return (
        key.startswith("numero de modelo ")
        or key.startswith("numero de pieza ")
        or key.startswith("referencia ") and key.endswith(" fabricante")
    )


def _load_json(name: str) -> dict[str, str]:
    path = PACKAGE_DICTIONARIES / name
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"dictionary must be an object: {path}")
    return {str(k): str(v) for k, v in data.items() if str(k).strip()}


class DictionaryService:
    """One lookup surface for categories, labels, values and protected terms."""

    def __init__(self, directory: Optional[str | Path] = None):
        root = Path(directory) if directory else PACKAGE_DICTIONARIES
        self.directory = root
        self.categories = self._load(root, "categories.json")
        self.attribute_labels = self._load(root, "attribute_labels.json")
        self.units = self._load(root, "units.json")
        self.materials = self._load(root, "materials.json")
        self.colors = self._load(root, "colors.json")
        self.booleans = self._load(root, "booleans.json")
        self.packaging = self._load(root, "packaging.json")
        self.protected_terms = self._load(root, "protected_terms.json")
        self._indexes = {
            name: self._normalized_index(name, values)
            for name, values in {
                "categories": self.categories,
                "attribute_labels": self.attribute_labels,
                "units": self.units,
                "materials": self.materials,
                "colors": self.colors,
                "booleans": self.booleans,
                "packaging": self.packaging,
                "protected_terms": self.protected_terms,
            }.items()
        }

    @staticmethod
    def _normalized_index(name: str, values: Mapping[str, str]) -> dict[str, str]:
        index: dict[str, str] = {}
        for raw, target in values.items():
            key = normalize_key(raw)
            previous = index.get(key)
            if previous is not None and previous != target:
                raise ValueError("NORMALIZED_DICTIONARY_COLLISION:%s:%s" % (name, key))
            index[key] = target
        return index

    @staticmethod
    def _load(root: Path, name: str) -> dict[str, str]:
        path = root / name
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise ValueError(f"dictionary must be an object: {path}")
        return {str(k): str(v) for k, v in data.items() if str(k).strip()}

    def lookup_exact(self, value: Any, dictionary: str) -> Optional[str]:
        return getattr(self, dictionary, {}).get(str(value or "").strip())

    def lookup_normalized(self, value: Any, dictionary: str) -> Optional[str]:
        return self._indexes.get(dictionary, {}).get(normalize_key(value))

    def lookup_category(self, value: Any) -> Optional[str]:
        return self.lookup_normalized(value, "categories")

    def lookup_attribute_label(self, value: Any) -> Optional[str]:
        return self.lookup_normalized(value, "attribute_labels")

    def lookup_unit(self, value: Any) -> Optional[str]:
        return self.lookup_normalized(value, "units")

    def lookup_value(self, value: Any, *, field: str = "") -> Optional[str]:
        text = str(value or "").strip()
        # Boolean matching is exact/whole-field only.  In particular, never
        # replace ``No`` inside a normal Spanish sentence.
        if normalize_key(text) in self._indexes["booleans"]:
            return self._indexes["booleans"][normalize_key(text)]
        if field in {"material", "materials"}:
            return self.lookup_normalized(text, "materials")
        if field in {"color", "colors"}:
            return self.lookup_normalized(text, "colors")
        if field in {"packaging", "variation", "selected_variant_es",
                     "selected_variation_raw"}:
            return self.lookup_normalized(text, "packaging")
        return (self.lookup_normalized(text, "materials")
                or self.lookup_normalized(text, "colors")
                or self.lookup_normalized(text, "packaging"))

    def is_protected(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if self.lookup_normalized(text, "protected_terms") is not None:
            return True
        return bool(re.search(
            r"(?:\b[A-Z]{2,}[A-Z0-9+.-]*\b|\b\d{4,}\b|\b[A-Z]?\d+[A-Z0-9-]*\b)",
            text))

    def protected_display(self, value: Any) -> str:
        """Return source formatting for an identity/technical token."""
        return str(value or "").strip()

    def dictionary_counts(self) -> dict[str, int]:
        return {
            "categories": len(self.categories),
            "attribute_labels": len(self.attribute_labels),
            "units": len(self.units),
            "materials": len(self.materials),
            "colors": len(self.colors),
            "booleans": len(self.booleans),
            "packaging": len(self.packaging),
            "protected_terms": len(self.protected_terms),
        }


def resolve_exact(service: DictionaryService, value: Any, *, kind: str,
                  field: str = "") -> dict[str, str]:
    """Resolve one atomic value and retain its source/provenance status."""
    source = str(value or "")
    if not source.strip():
        return {"source_text": source, "resolved_text": "", "status": "source_missing",
                "resolution_source": "source_missing"}
    if kind == "category":
        translated = service.lookup_category(source)
        origin = "dictionary" if translated is not None else "unresolved"
    elif kind == "attribute_label":
        translated = service.lookup_attribute_label(source)
        origin = "dictionary" if translated is not None else "unresolved"
    elif kind == "unit":
        translated = service.lookup_unit(source)
        origin = "dictionary" if translated is not None else "unresolved"
    elif kind in {"value", "material", "color", "boolean", "packaging", "variation"}:
        translated = service.lookup_value(source, field=field or kind)
        origin = "dictionary" if translated is not None else "unresolved"
    elif service.is_protected(source):
        translated = service.protected_display(source)
        origin = "protected"
    else:
        translated = None
        origin = "unresolved"
    if translated is None:
        return {"source_text": source, "resolved_text": source,
                "status": "unresolved", "resolution_source": "unresolved"}
    return {"source_text": source, "resolved_text": translated,
            "status": "resolved", "resolution_source": origin}
