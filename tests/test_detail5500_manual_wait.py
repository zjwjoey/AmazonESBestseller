from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import json


def test_visible_challenge_wait_keeps_page_and_does_not_navigate_or_resume(tmp_path):
    from amazon_es_bestseller.collection.detail5500_runtime import wait_for_manual_assistance
    marker = tmp_path / 'manual_assistance.close.requested'
    ticks = []
    class Page:
        def is_closed(self): return False
        def wait_for_timeout(self, ms):
            ticks.append(ms)
            assert session.access_blocked is True
            assert json.loads((tmp_path/'worker_status.json').read_text())['phase']=='WAITING_MANUAL_ASSISTANCE'
            marker.write_text('user handoff', encoding='utf8')
    session = SimpleNamespace(page=Page(), access_blocked=False)
    result = wait_for_manual_assistance(session, tmp_path, datetime.now(timezone.utc)+timedelta(hours=1), {'reason':'DETAIL_ACCESS_STOP CHALLENGE'})
    assert result=='EXPLICIT_HANDOFF_CLOSE_REQUESTED' and ticks==[1000]
    status=json.loads((tmp_path/'worker_status.json').read_text())
    assert status['reason']=='DETAIL_ACCESS_STOP CHALLENGE'
    assert status['collection_paused'] and status['automatic_resume'] is False
    assert not list(tmp_path.glob('*gate*')) and not list(tmp_path.glob('*receipt*'))


def test_closed_window_and_deadline_end_wait_without_retry(tmp_path):
    from amazon_es_bestseller.collection.detail5500_runtime import wait_for_manual_assistance
    session=SimpleNamespace(page=SimpleNamespace(is_closed=lambda:True),access_blocked=False)
    assert wait_for_manual_assistance(session,tmp_path,datetime.now(timezone.utc)+timedelta(hours=1),{})=='WINDOW_CLOSED_BY_USER'
    session.page.is_closed=lambda:False
    assert wait_for_manual_assistance(session,tmp_path,datetime.now(timezone.utc)-timedelta(seconds=1),{})=='FINAL_DEADLINE_REACHED'


def test_stale_close_marker_and_normal_DOM_do_not_resume_or_close_new_wait(tmp_path):
    from amazon_es_bestseller.collection.detail5500_runtime import wait_for_manual_assistance
    marker=tmp_path/'manual_assistance.close.requested';marker.write_text('old request')
    gate=tmp_path/'access_gate.json';gate.write_bytes(b'preserved original challenge')
    ticks=[]
    class Page:
        def is_closed(self): return False
        def content(self): return '<h1>Normal product</h1>'
        def wait_for_timeout(self, ms):
            ticks.append(ms)
            if len(ticks)==3:marker.write_text('new explicit handoff request')
    session=SimpleNamespace(page=Page(),access_blocked=False)
    assert wait_for_manual_assistance(session,tmp_path,datetime.now(timezone.utc)+timedelta(hours=1),{})=='EXPLICIT_HANDOFF_CLOSE_REQUESTED'
    assert ticks==[1000]*3 and gate.read_bytes()==b'preserved original challenge'
