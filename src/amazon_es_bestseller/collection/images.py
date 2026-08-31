# -*- coding: utf-8 -*-
"""Traceable ASIN-keyed original image cache (serial, bounded)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.request import Request, urlopen


def download_images(records, out_dir, delay_seconds: float = 1.0, fetcher=None) -> dict:
    """Download missing images as ``<ASIN>.<ext>`` and return per-ASIN outcomes."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    fetch = fetcher or _fetch
    results = {}
    for record in records:
        asin = str(record.get("asin") or "").strip().upper()
        url = str(record.get("image_url") or "").strip()
        if not asin or not url.startswith("http"):
            continue
        target = next((p for p in root.glob(asin + ".*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
        if target:
            results[asin] = {"status": "cached", "path": str(target)}
            continue
        try:
            content_type, body = fetch(url)
            ext = ".png" if "png" in content_type.lower() else ".jpg"
            target = root / (asin + ext)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(body)
            os.replace(temporary, target)
            results[asin] = {"status": "downloaded", "path": str(target), "source_url": url}
        except Exception as exc:
            results[asin] = {"status": "failed", "error": str(exc), "source_url": url}
        if delay_seconds:
            time.sleep(delay_seconds)
    return results


def _fetch(url):
    request = Request(url, headers={"User-Agent": "AmazonESBestseller/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.headers.get("Content-Type", "image/jpeg"), response.read()
