import hashlib
import json
import pytest

from amazon_es_bestseller.multisource.storage import immutable_json, sealed
from amazon_es_bestseller.highspeed.v4_workflows import export_v4


def protocol_receipt(tmp_path):
    # Offline protocol fixture, never an observed live HTTP transaction.
    body = tmp_path / 'body.html'; body.write_bytes(b'<html>protocol fixture</html>')
    payload = {'request_id': 'test_request', 'run_id': 'test_run', 'origin': 'LIVE_CAPTURE',
               'source_type': 'BEST_SELLERS', 'html_path': str(body),
               'sha256': hashlib.sha256(body.read_bytes()).hexdigest(),
               'status_code': 200, 'access_state': 'NORMAL', 'duration_seconds': 2.5}
    receipt = tmp_path / 'receipt.json'; immutable_json(receipt, sealed(payload))
    return {**payload, 'evidence_path': str(receipt), 'duration_seconds': 99999}


def test_performance_reloads_actual_sealed_fields_not_caller_summary(tmp_path):
    receipt = protocol_receipt(tmp_path)
    result = export_v4([], [], [], tmp_path / 'out', run_id='test_run', stage='T1', request_receipts=[receipt])
    from pathlib import Path
    perf = json.loads((Path(result['directory']) / 'performance_v4.json').read_text(encoding='utf8'))
    assert perf['collection_request_operation_seconds'] == 2.5
    assert perf['live_request_success_rate'] == 1
    assert perf['actual_request_receipt_count'] == 1
    assert perf['ten_hour_collection_goal_demonstrated'] is False


def test_duplicate_receipts_cannot_inflate_request_success(tmp_path):
    receipt = protocol_receipt(tmp_path)
    with pytest.raises(ValueError, match='DUPLICATE_RECEIPT'):
        export_v4([], [], [], tmp_path / 'out', run_id='test_run', stage='T1', request_receipts=[receipt, receipt])


def test_changed_saved_body_rejects_performance_projection(tmp_path):
    receipt = protocol_receipt(tmp_path)
    from pathlib import Path
    Path(receipt['html_path']).write_bytes(b'<html>changed</html>')
    with pytest.raises(ValueError, match='BODY_SHA_MISMATCH'):
        export_v4([], [], [], tmp_path / 'out', run_id='test_run', stage='T1', request_receipts=[receipt])
