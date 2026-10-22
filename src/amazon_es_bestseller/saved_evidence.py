"""Bounded, read-only saved-HTML reuse for one offline audit invocation."""
from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from pathlib import Path


class SavedEvidenceChanged(RuntimeError):
    """An input changed while the audit was using it; never reuse stale bytes."""


def html_asin(html: str) -> str:
    for pattern in (
        r'<input[^>]+(?:id|name)=["\']ASIN["\'][^>]+value=["\']([A-Z0-9]{10})["\']',
        r'<input[^>]+value=["\']([A-Z0-9]{10})["\'][^>]+(?:id|name)=["\']ASIN["\']',
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'][^"\']*/dp/([A-Z0-9]{10})',
    ):
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1).upper()
    return ""


class SavedHtmlIndex:
    """No persistent cache: directory index, hashes and a bounded body LRU."""

    def __init__(self, roots=None, *, max_bodies: int = 8, progress=None):
        roots = [roots] if isinstance(roots, (str, Path)) else list(roots or [])
        self.roots = [Path(root) for root in roots]
        self.max_bodies = max(1, max_bodies)
        self._paths = None
        self._bodies = OrderedDict()
        self._signatures = {}
        self.sha256 = {}
        self._indices = {}
        self.read_count = 0
        self.progress = progress

    def paths(self):
        if self._paths is None:
            paths = []
            for root in self.roots:
                paths.extend(sorted(root.rglob("*.html")) if root.is_dir() else [root])
            self._paths = list(dict.fromkeys(paths))
        return self._paths

    @staticmethod
    def _signature(path):
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def read(self, path):
        path = Path(path).resolve()
        before = self._signature(path)
        if path in self._signatures and before != self._signatures[path]:
            raise SavedEvidenceChanged("Saved evidence changed during audit: %s" % path)
        if path in self._bodies:
            self._bodies.move_to_end(path)
            return self._bodies[path]
        body = path.read_bytes()
        if self._signature(path) != before:
            raise SavedEvidenceChanged("Saved evidence changed during read: %s" % path)
        self.read_count += 1
        self._signatures[path] = before
        digest = hashlib.sha256(body).hexdigest()
        if path in self.sha256 and digest != self.sha256[path]:
            raise SavedEvidenceChanged("Saved evidence hash changed during audit: %s" % path)
        self.sha256[path] = digest
        text = body.decode("utf-8", errors="replace")
        self._bodies[path] = text
        if len(self._bodies) > self.max_bodies:
            self._bodies.popitem(last=False)
        return text

    def detail_paths(self, asins=None, *, prefer_filename=False):
        selected = {str(value).upper() for value in asins or ()}
        key = (tuple(sorted(selected)), prefer_filename)
        if key not in self._indices:
            found = {}
            paths = self.paths()
            for position, path in enumerate(paths, 1):
                if self.progress:
                    self.progress({"event": "progress", "check": "evidence_index",
                                   "completed": position, "total": len(paths)})
                named = path.stem.upper() if re.fullmatch(r"[A-Z0-9]{10}", path.stem, re.I) else ""
                if selected and named and named not in selected:
                    continue
                if named and prefer_filename:
                    asin = named
                else:
                    try:
                        asin = html_asin(self.read(path)) or named
                    except OSError:
                        continue
                if asin and (not selected or asin in selected):
                    found.setdefault(asin, path)
            self._indices[key] = found
        return self._indices[key]

    def by_asin(self, asins=None):
        return _HtmlView(self, self.detail_paths(asins))


class _HtmlView(Mapping):
    def __init__(self, index, paths):
        self.index, self.paths = index, paths

    def __getitem__(self, asin):
        return self.index.read(self.paths[asin])

    def __iter__(self):
        return iter(self.paths)

    def __len__(self):
        return len(self.paths)
