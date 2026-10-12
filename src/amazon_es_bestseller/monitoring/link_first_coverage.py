"""Fresh coverage extension: existing current-batch rows supply identities only."""
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..orchestration.checkpoint import read_json
from ..orchestration.link_first import LinkFirstRunner, canonical_json_sha256, file_hash


class CoverageRunner(LinkFirstRunner):
    def __init__(self, *args, exclude_current_details, **kwargs):
        self.exclusion_files = {str(Path(p).resolve()): file_hash(Path(p))
                                for p in exclude_current_details}
        if not self.exclusion_files:
            raise ValueError("CURRENT_BATCH_EXCLUSION_REQUIRED")
        self.excluded_asins = set()
        for path in self.exclusion_files:
            rows = read_json(Path(path), None)
            if not isinstance(rows, list):
                raise ValueError("INVALID_CURRENT_BATCH_IDENTITY_INPUT")
            for row in rows:
                asin = row.get("asin", "") if isinstance(row, dict) else ""
                if not isinstance(asin, str) or not re.fullmatch(r"[A-Z0-9]{10}", asin):
                    raise ValueError("INVALID_CURRENT_BATCH_IDENTITY")
                self.excluded_asins.add(asin)
            if file_hash(Path(path)) != self.exclusion_files[path]:
                raise ValueError("CURRENT_BATCH_EXCLUSION_DRIFT")
        super().__init__(*args, **kwargs)
        if self.excluded_asins.intersection(self.selected):
            raise ValueError("EXCLUDED_IDENTITY_SELECTED_IN_CHECKPOINT")

    def runtime_metadata(self):
        return {"coverage_contract": "fresh-remaining-category-coverage-v1",
                "excluded_current_files": self.exclusion_files,
                "excluded_current_unique": len(self.excluded_asins),
                "excluded_current_asins_sha256": canonical_json_sha256(sorted(self.excluded_asins)),
                "previous_details_reuse": False,
                "current_batch_inputs_usage": "identity_exclusion_only"}

    def guards(self):
        super().guards()
        for path, expected in self.exclusion_files.items():
            if file_hash(Path(path)) != expected:
                raise ValueError("CURRENT_BATCH_EXCLUSION_DRIFT")

    def selection_eligible(self, row, link):
        return row["asin"] not in self.excluded_asins

    def _eligible_links(self, category):
        # URL identity is only an exclusion hint, never a confirmed product
        # identity. Unknown URLs still require detail-page identity resolution.
        links = super()._eligible_links(category)
        def keep(link):
            match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)",
                              urlsplit(link["product_link"]).path)
            return not match or match.group(1) not in self.excluded_asins
        return [link for link in links if keep(link)]
