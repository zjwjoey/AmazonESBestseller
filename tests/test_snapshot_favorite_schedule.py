from copy import deepcopy
from amazon_es_bestseller.highspeed.page_snapshot import promote_favorite_page,prioritize_saved_pages,CurrentPageSnapshotRunner

def item(kind,url,category='Hogar'):
    return ({'source_type':kind,'collection_category_key':category},url)

def test_favorite_next_cannot_wait_behind_hundreds_of_main_children():
    work=[item('BEST_SELLERS','current'),*[item('NEW_RELEASES',str(i)) for i in range(250)],
          item('CUSTOMER_FAVORITES','page3'),item('BEST_SELLERS','other','DIY')]
    promote_favorite_page(work,0,minimum_met=False)
    assert work[1][1]=='page3' and work[-1][1]=='other'

def test_alternating_turn_keeps_main_sources_running():
    work=[item('CUSTOMER_FAVORITES','page3'),item('NEW_RELEASES','main'),item('CUSTOMER_FAVORITES','page4')]
    expected=deepcopy(work);promote_favorite_page(work,0,minimum_met=False)
    assert work==expected
    promote_favorite_page(work,1,minimum_met=False)
    assert work[2][1]=='page4'

def test_met_favorite_floor_and_other_department_never_get_promoted():
    work=[item('BEST_SELLERS','current'),item('NEW_RELEASES','main'),item('CUSTOMER_FAVORITES','next')]
    expected=deepcopy(work);promote_favorite_page(work,0,minimum_met=True);assert work==expected
    work[-1][0]['collection_category_key']='DIY';expected=deepcopy(work)
    promote_favorite_page(work,0,minimum_met=False);assert work==expected

def test_resume_prioritizes_saved_then_under_quota_favorite_inside_category():
    work=[item('BEST_SELLERS','main'),item('CUSTOMER_FAVORITES','favorite'),item('NEW_RELEASES','saved'),
          item('CUSTOMER_FAVORITES','other','DIY')]
    receipt={'request_url':'saved','kind':'PAGE','status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE'}
    ordered=prioritize_saved_pages(work,[receipt],set(),['Hogar','DIY'],favorites_unmet=True)
    assert [u for _,u in ordered]==['saved','favorite','main','other']

def test_total_alone_never_triggers_successful_stop():
    runner=CurrentPageSnapshotRunner.__new__(CurrentPageSnapshotRunner)
    runner._snapshot_metrics={'F_global_conservative_review_groups':20000,'target_met':False,
        'sources':{'CUSTOMER_FAVORITES':{'E_qualified_review_groups':2999,'target_met':False}}}
    assert runner.target_stop_reason({}) is None
    assert runner.should_collect_source({}, {'source_type':'CUSTOMER_FAVORITES'})
