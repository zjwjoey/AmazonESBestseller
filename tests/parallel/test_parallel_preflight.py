import hashlib
import pytest
from amazon_es_bestseller.highspeed.parallel_preflight import verify_code_manifest
from amazon_es_bestseller.highspeed.parallel_preflight import verify_isolated_acceptance


def test_complete_code_manifest_rejects_changed_missing_and_outside_paths(tmp_path):
    files={}
    for name in ('transport','commit','coordinator','admission','dispatch_gate','page_ledger','amendment','preflight','checkpoint','cli','alias_review','faults'):
        relative=f'src/amazon_es_bestseller/highspeed/parallel_{name}.py'
        path=tmp_path/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('TEST_ONLY',encoding='utf8')
        files[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    verify_code_manifest(tmp_path,{'files':files})
    with pytest.raises(ValueError,match='COMPLETE'):verify_code_manifest(tmp_path,{'files':{}})
    with pytest.raises(ValueError,match='PATH'):
        verify_code_manifest(tmp_path,{'files':{**files,'../outside':'x'}})
    (tmp_path/next(iter(files))).write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='BYTES_CHANGED'):verify_code_manifest(tmp_path,{'files':files})


def test_acceptance_status_alone_cannot_authorize_live(tmp_path):
    with pytest.raises(ValueError,match='ACCEPTANCE_HASH'):
        verify_isolated_acceptance({'run_id':'TEST_ONLY'},{'acceptance_status':'ISOLATED_ACCEPTANCE_PASSED'},{'files':{}})


def test_old_checkout_cannot_validate_manifest_for_a_different_loaded_release(tmp_path):
    from amazon_es_bestseller.highspeed.parallel_preflight import check_parallel_start
    with pytest.raises(ValueError,match='LOADED_PARALLEL_CODE_ROOT_MISMATCH'):
        check_parallel_start(None,None,None,None,None,code_root=tmp_path,now=None)
