"""Saved real native contracts: session reuse never ignores changed bytes."""
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from test_highspeed_v4_loading import dynamic
from amazon_es_bestseller.highspeed import v4_loading as loading
from amazon_es_bestseller.highspeed import v4_adapters as adapters
from amazon_es_bestseller.highspeed.v4_authorization import prerequisite_review_session
from amazon_es_bestseller.multisource.storage import immutable_json


def copied_contract(tmp_path):
    source, desc = dynamic(tmp_path, 'AMAZON_HAUL')
    parent = tmp_path/'parent.html'
    parent.write_bytes(Path(desc['parent_html_path']).read_bytes())
    desc['parent_html_path'] = str(parent)
    file = tmp_path/'copied_contract.json'
    native = {k:v for k,v in desc.items() if k not in {'native_contract_evidence_path','native_contract_evidence_sha256'}}
    immutable_json(file,native)
    desc.update(native_contract_evidence_path=str(file),
                native_contract_evidence_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    return source, desc


def test_saved_dynamic_validation_reuses_only_inside_review_session(tmp_path):
    source,desc=copied_contract(tmp_path)
    args=(desc['url'],source,{'allow_native_dynamic':True},{'run_id':desc['run_id']},desc)
    with patch.object(adapters,'parse_v4_page',wraps=adapters.parse_v4_page) as full:
        with prerequisite_review_session():
            loading.validate_dynamic_descriptor(*args)
            loading.validate_dynamic_descriptor(*args)
            assert full.call_count==1
        loading.validate_dynamic_descriptor(*args)
        loading.validate_dynamic_descriptor(*args)
        assert full.call_count==3


@pytest.mark.parametrize('changed',['parent','native','source','scope','run','descriptor'])
def test_changed_dynamic_dependency_or_context_cannot_reuse_proof(tmp_path,changed):
    source,desc=copied_contract(tmp_path)
    scope={'allow_native_dynamic':True};auth={'run_id':desc['run_id']}
    with prerequisite_review_session():
        loading.validate_dynamic_descriptor(desc['url'],source,scope,auth,desc)
        if changed=='parent':Path(desc['parent_html_path']).write_text('changed',encoding='utf8')
        elif changed=='native':Path(desc['native_contract_evidence_path']).write_text('{}',encoding='utf8')
        elif changed=='source':source={**source,'source_id':'wrong'}
        elif changed=='scope':scope={'allow_native_dynamic':False}
        elif changed=='run':auth={'run_id':'wrong'}
        else:desc={**deepcopy(desc),'payload':'{}'}
        with pytest.raises(ValueError):
            loading.validate_dynamic_descriptor(desc['url'],source,scope,auth,desc)
