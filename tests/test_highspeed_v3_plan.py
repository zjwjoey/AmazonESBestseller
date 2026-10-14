import pytest
from amazon_es_bestseller.highspeed.budgets import validate_plan
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION

def plan(**extra):
    return new_plan('v3', snapshot_contract_version=3, normalizer_version=NORMALIZER_VERSION,
        sources=[{'source_id':'s','source_type':'BEST_SELLERS','parser_profile':'V3',
                  'page_urls':['https://www.amazon.es/gp/bestsellers']}], **extra)

def test_v3_explicit_policy_and_no_legacy_seed():
    assert validate_plan(plan())
    p=plan(stage='T3',origin='LIVE_CAPTURE',new_batch_required=True)
    assert validate_plan(p)
    p['parent_run_id']='old'
    with pytest.raises(ValueError,match='FRESH_BATCH'): validate_plan(p)

@pytest.mark.parametrize('field,value', [('snapshot_contract_version',True),
    ('snapshot_contract_version',4),('normalizer_version','legacy'),('request_pacing_seconds',2)])
def test_v3_policy_cannot_silently_fall_back(field,value):
    p=plan();p[field]=value
    with pytest.raises(ValueError): validate_plan(p)
