"""Retired entries stop before stores/browsers; approved PAGE code stays separate."""
import json
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.cli import build_parser, main
from amazon_es_bestseller.highspeed.cli import dispatch as highspeed
from amazon_es_bestseller.highspeed.v4_cli import run_live_v4
from amazon_es_bestseller.multisource.cli import dispatch as multisource
from amazon_es_bestseller.multisource.storage import immutable_json, sealed
from test_highspeed_readonly_cli import fingerprint


def test_ds_absent_from_public_help_but_compatibility_stop_preserves_inputs(tmp_path):
    assert 'translate-ds' not in build_parser().format_help()
    with pytest.raises(SystemExit,match='TRANSLATION_API_REMOVED'):
        main(['--offline','translate-ds','--products',str(tmp_path/'missing'),'--out',str(tmp_path/'out')])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('operation',['run','resume'])
def test_old_highspeed_live_stops_before_store_creation(tmp_path,operation):
    plan = tmp_path/'plan.json';plan.write_text(json.dumps({'stage':'T1','run_id':'TEST_ONLY'}),encoding='utf-8')
    root = tmp_path/'absent'
    args = build_parser().parse_args(['highspeed',operation,'--root',str(root),'--plan',str(plan),'--live'])
    with pytest.raises(ValueError,match='LEGACY_HIGHSPEED_LIVE_RETIRED'):highspeed(args)
    assert not root.exists()


@pytest.mark.parametrize('live_flag',[True,False])
def test_multisource_explicit_or_implicit_live_is_retired_before_creation(tmp_path,live_flag):
    plan = tmp_path/'plan.json';plan.write_text(json.dumps({'origin':'LIVE_CAPTURE'}),encoding='utf-8')
    root = tmp_path/'absent'
    argv = ['multisource','run','--root',str(root),'--plan',str(plan)]
    if live_flag:argv.append('--live')
    with pytest.raises(ValueError,match='LEGACY_MULTISOURCE_LIVE_RETIRED'):
        multisource(build_parser().parse_args(argv))
    assert not root.exists()


def test_stored_live_resume_rejects_without_seeding_or_changing_existing_state(tmp_path):
    root = tmp_path/'existing'
    immutable_json(root/'runs'/'TEST_ONLY'/'run_spec.json',sealed({'origin':'LIVE_CAPTURE'}))
    before = fingerprint(root)
    args = build_parser().parse_args(['multisource','resume','--root',str(root),'--run-id','TEST_ONLY'])
    with pytest.raises(ValueError,match='LEGACY_MULTISOURCE_LIVE_RETIRED'):multisource(args)
    assert fingerprint(root) == before


def args_for_v4(tmp_path, *, page=False, diagnostics=False):
    root = tmp_path/'TEST_ONLY'/'store'
    plan = {'run_id':'TEST_ONLY','stage':'T1' if page else 'T2'}
    if page:plan['collection_contract']='CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1'
    path = tmp_path/'plan.json';path.write_text(json.dumps(plan),encoding='utf-8')
    auth = tmp_path/'auth.json';auth.write_text('{}',encoding='utf-8')
    return SimpleNamespace(operation='run',root=str(root),plan=str(path),authorization=str(auth),live=True,
        experimental_protocol_diagnostics=diagnostics,profile_dir=None,headful=False)


def test_generic_dynamic_entry_requires_explicit_diagnostics_before_store(tmp_path):
    args = args_for_v4(tmp_path)
    with pytest.raises(ValueError,match='LEGACY_DYNAMIC_ENTRY_RETIRED'):run_live_v4(args)
    assert not (tmp_path/'TEST_ONLY').exists()


@pytest.mark.parametrize('page,diagnostics',[(True,False),(False,True)])
def test_current_page_or_diagnostic_flag_still_requires_original_authorization(tmp_path,monkeypatch,page,diagnostics):
    from amazon_es_bestseller.access.browser import BrowserSession
    monkeypatch.setattr(BrowserSession,'__init__',lambda *a,**k:pytest.fail('UNAUTHORIZED_BROWSER'))
    args = args_for_v4(tmp_path,page=page,diagnostics=diagnostics)
    with pytest.raises(ValueError) as error:run_live_v4(args)
    assert 'LEGACY_DYNAMIC_ENTRY_RETIRED' not in str(error.value)
