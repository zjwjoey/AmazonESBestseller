"""Detail-only routing must not be bypassed by Service Worker requests."""
import sys
from types import SimpleNamespace as NS
import pytest


@pytest.mark.parametrize('profile',[None,'unchanged-profile'])
def test_opt_in_blocks_service_workers_before_any_detail_page(profile,monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    calls=[]
    class Context:
        def new_page(self,**kwargs):calls.append(('page',kwargs));return object()
        def close(self):pass
    context=Context()
    def persistent(**kwargs):calls.append(('persistent',kwargs));return context
    def launch(**kwargs):calls.append(('launch',kwargs));return context
    chromium=NS(launch_persistent_context=persistent,launch=launch)
    fake=NS(start=lambda:NS(chromium=chromium,stop=lambda:None))
    monkeypatch.setitem(sys.modules,'playwright.sync_api',NS(sync_playwright=lambda:fake))
    with BrowserSession(profile_dir=profile,block_service_workers=True):pass
    kind='persistent' if profile else 'page'
    assert next(kw for name,kw in calls if name==kind)['service_workers']=='block'
    if profile:assert calls[0][1]['user_data_dir']==profile


def test_default_other_collectors_browser_settings_unchanged(monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    calls=[]
    context=NS(new_page=lambda **kw:calls.append(kw),close=lambda:None)
    chromium=NS(launch_persistent_context=lambda **kw:(calls.append(kw) or context))
    fake=NS(start=lambda:NS(chromium=chromium,stop=lambda:None))
    monkeypatch.setitem(sys.modules,'playwright.sync_api',NS(sync_playwright=lambda:fake))
    with BrowserSession(profile_dir='original'):pass
    assert all('service_workers' not in c for c in calls)
