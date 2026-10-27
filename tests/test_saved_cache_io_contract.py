"""Saved-page selection and IO denial are not evidence or item failures."""
import builtins
import io
import json
from pathlib import Path
import shutil

import pytest

from amazon_es_bestseller.collection import detail
from amazon_es_bestseller.collection.planning import DetailState

ASIN = 'B07QHCNQ7M'
FIXTURE = Path(__file__).parent / 'fixtures/html/detail500_20261022' / (ASIN + '.html')


class NoNetwork:
    def goto(self, *_a, **_kw):
        pytest.fail('saved-cache IO must never trigger a request')

    def wait_between_requests(self):
        pytest.fail('a cached result must not pace a network request')


def saved(tmp_path):
    folder = tmp_path / 'html'
    folder.mkdir()
    path = folder / FIXTURE.name
    shutil.copy2(FIXTURE, path)
    meta = path.with_suffix('.meta.json')
    meta.write_text(json.dumps({'status_code': 200, 'final_url': 'https://www.amazon.es/dp/' + ASIN}), encoding='utf-8')
    return path, meta


def test_selected_asin_is_filtered_before_unrelated_html_is_opened(tmp_path, monkeypatch):
    selected, _ = saved(tmp_path)
    unselected = selected.parent / 'B000000099.html'
    unselected.write_text('unselected Robot Check evidence', encoding='utf-8')
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == unselected:
            pytest.fail('must not open a named ASIN outside the selected set')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    state = DetailState(tmp_path / 'state.json')
    result = detail.reparse_saved_details(selected.parent, state, asins=[ASIN], parser_version='v2')
    assert [row['asin'] for row in result] == [ASIN]
    assert unselected.read_bytes() == b'unselected Robot Check evidence'


def test_legacy_unknown_filename_still_uses_saved_identity_evidence(tmp_path):
    folder = tmp_path / 'html'
    folder.mkdir()
    shutil.copy2(FIXTURE, folder / 'page_01.html')
    state = DetailState(tmp_path / 'state.json')
    result = detail.reparse_saved_details(folder, state, asins=[ASIN], parser_version='v2')
    assert [row['asin'] for row in result] == [ASIN]


@pytest.mark.parametrize('file_kind', ['html', 'metadata'])
@pytest.mark.parametrize('operation', ['resume', 'offline_replay'])
def test_permission_failure_preserves_evidence_and_existing_checkpoint(tmp_path, monkeypatch, file_kind, operation):
    html, meta = saved(tmp_path)
    checkpoint = tmp_path / 'checkpoints' / (ASIN + '.json')
    checkpoint.parent.mkdir()
    checkpoint.write_text('{"historical":"keep"}', encoding='utf-8')
    old = {p: p.read_bytes() for p in [html, meta, checkpoint]}
    target = html if file_kind == 'html' else meta
    open_builtin, open_io = builtins.open, io.open
    def guard(original):
        def open_file(file, *args, **kwargs):
            if not isinstance(file, int) and Path(file) == target:
                error = PermissionError(13, 'injected persistent file denial')
                error.winerror = 5
                raise error
            return original(file, *args, **kwargs)
        return open_file
    monkeypatch.setattr(builtins, 'open', guard(open_builtin))
    monkeypatch.setattr(io, 'open', guard(open_io))
    with pytest.raises(PermissionError):
        if operation == 'resume':
            detail.collect_details([ASIN], NoNetwork(), tmp_path, parser_version='v2')
        else:
            detail.reparse_saved_details(html.parent, DetailState(tmp_path / 'state.json'),
                                         asins=[ASIN], parser_version='v2')
    monkeypatch.undo()
    assert all(p.read_bytes() == value for p, value in old.items())


def test_transient_metadata_lock_is_retried_without_a_false_cache_failure(tmp_path, monkeypatch):
    html, meta = saved(tmp_path)
    original = Path.read_text
    attempts = []
    def read(path, *args, **kwargs):
        if path == meta:
            attempts.append(True)
            if len(attempts) == 1:
                error = PermissionError(13, 'injected sharing violation')
                error.winerror = 32
                raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    from amazon_es_bestseller.collection import runtime_io
    monkeypatch.setattr(runtime_io.time, 'sleep', lambda _: None)
    rows = detail.collect_details([ASIN], NoNetwork(), tmp_path, parser_version='v2')
    assert len(rows) == 1 and rows[0]['asin'] == ASIN
    assert len(attempts) == 2
