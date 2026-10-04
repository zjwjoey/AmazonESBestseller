import json

import pytest

from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_html
from amazon_es_bestseller.monitoring.ranking_identity.snapshot import write_identity_snapshot


def test_identity_snapshot_writes_contract_and_is_append_only(tmp_path):
    result = extract_identity_from_html(
        "<div id='gridItemRoot'><a href='/dp/B078C6QR1C'>x</a></div>",
        source_url="https://www.amazon.es/zgbs/1", expected_count=1)
    saved = write_identity_snapshot(result, tmp_path, snapshot_id="identity_snapshot_test")
    assert json.loads((saved["path"] / "identity.json").read_text(encoding="utf-8"))[0]["product_url"] == "https://www.amazon.es/dp/B078C6QR1C"
    assert json.loads((saved["path"] / "manifest.json").read_text(encoding="utf-8"))["status"] == "IDENTITY_COMPLETE"
    with pytest.raises(FileExistsError):
        write_identity_snapshot(result, tmp_path, snapshot_id="identity_snapshot_test")
