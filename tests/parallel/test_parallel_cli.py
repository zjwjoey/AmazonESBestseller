import json
import pytest
from amazon_es_bestseller.highspeed import parallel_cli


def test_help_never_constructs_store_or_browser(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('STORE_OPENED')
    monkeypatch.setattr(parallel_cli,'RunStore',forbidden)
    monkeypatch.setattr(parallel_cli,'ReadOnlyRunStore',forbidden)
    with pytest.raises(SystemExit) as e:parallel_cli.main(['--help'])
    assert e.value.code==0


def test_resume_requires_explicit_live_and_pause_release_before_store(tmp_path,monkeypatch):
    file=tmp_path/'fixture.json';file.write_text('{}',encoding='utf8')
    def forbidden(*args,**kwargs):raise AssertionError('STORE_OPENED')
    monkeypatch.setattr(parallel_cli,'RunStore',forbidden)
    args=['resume']
    for key in ('plan','authorization','amendment','manifest'):
        args+=['--'+key,str(file)]
    args+=['--root',str(tmp_path/'store'),'--code-root',str(tmp_path)]
    with pytest.raises(ValueError,match='EXPLICIT_LIVE'):parallel_cli.main(args)
