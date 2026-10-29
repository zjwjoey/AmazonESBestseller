import json
from pathlib import Path

import pytest
from scripts import install_snapshot_release as module


def test_release_switch_removes_old_launchers_and_preserves_local_edits_and_clock(tmp_path, monkeypatch):
    source=Path(__file__).resolve().parents[1]
    runtime=tmp_path/'old-runtime'
    package=runtime/'src/amazon_es_bestseller';package.mkdir(parents=True)
    (package/'__init__.py').write_text('LOCAL_EDIT_SENTINEL=42\n',encoding='utf-8')
    (package/'local_edit.py').write_text('LOCAL_EDIT_MUST_SURVIVE=42\n',encoding='utf-8')
    scripts=runtime/'scripts';scripts.mkdir()
    for name in ('Invoke-HighSpeed.ps1','Invoke-Multisource.ps1'):
        (scripts/name).write_text('TEST_ONLY old launcher',encoding='utf-8')
    (runtime/'运行程序.ps1').write_text('TEST_ONLY old default',encoding='utf-8')
    task=tmp_path/'task';(task/'store').mkdir(parents=True)
    clock=task/'task_clock.json';clock.write_text('{"deadline":"UNCHANGED"}',encoding='utf-8')
    data=task/'store/index.sqlite3';data.write_bytes(b'TEST_ONLY no SQLite connection permitted')
    monkeypatch.setattr(module,'assert_no_collector',lambda:None)
    before=module.protected_state(task)
    result=module.install(source,runtime,tmp_path/'new-release',protected_task=task,apply=True)
    assert result['protected_task_state_preserved'] and module.protected_state(task)==before
    assert not result['live_started'] and not result['hot_update']
    assert not (scripts/'Invoke-HighSpeed.ps1').exists()
    assert (package/'local_edit.py').read_text('utf-8')=='LOCAL_EDIT_MUST_SURVIVE=42\n'
    previous=Path(result['original_local_code_backup'])
    assert (previous/'src/amazon_es_bestseller/__init__.py').read_text('utf-8')=='LOCAL_EDIT_SENTINEL=42\n'
    assert 'TEST_ONLY old default' in (previous/'运行程序.ps1').read_text('utf-8')
    assert 'new-release' in (runtime/'运行程序.ps1').read_text('utf-8-sig')


def test_readiness_and_active_collector_guard_do_not_modify_runtime(tmp_path,monkeypatch):
    runtime=tmp_path/'runtime';(runtime/'src').mkdir(parents=True);(runtime/'scripts').mkdir()
    old=runtime/'src/keep.py';old.write_text('TEST_ONLY local edit',encoding='utf-8')
    before=module.files(runtime)
    def blocked():raise RuntimeError('COLLECTOR_ACTIVE_OR_PROCESS_CHECK_UNAVAILABLE')
    monkeypatch.setattr(module,'assert_no_collector',blocked)
    with pytest.raises(RuntimeError,match='COLLECTOR_ACTIVE'):
        module.install(Path(__file__).resolve().parents[1],runtime,tmp_path/'release',apply=True)
    assert module.files(runtime)==before and not (tmp_path/'release').exists()
