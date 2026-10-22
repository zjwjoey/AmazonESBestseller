import hashlib
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).parent/'fixtures'/'highspeed'

@pytest.mark.parametrize('path',sorted(ROOT.glob('real_v3_*.html')))
def test_real_v3_fixture_bytes_match_original_provenance(path):
    proof=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    expected=proof.get('sha256') or proof.get('fixture_sha256') or proof.get('html_sha256')
    assert expected, proof
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
