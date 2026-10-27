import pytest

from amazon_es_bestseller.translation.budget import BudgetLedger, VerifiedPriceCard


def price_card():
    return VerifiedPriceCard.from_mapping({
        'provider': 'test', 'model': 'test-model', 'currency': 'CNY',
        'input_per_million_cny': '1', 'output_per_million_cny': '2',
        'verified_at': '2026-10-25T00:00:00Z',
        'source': 'https://help.aliyun.com/pricing/qwen-mt',
    }, provider='test', model='test-model')


def test_new_nested_budget_directory_is_created_before_file_lock(tmp_path):
    path = tmp_path / 'new-run' / 'budget' / 'translation_budget_ledger.json'
    assert not path.parent.exists()
    ledger = BudgetLedger(path, limit_cny='5', price_card=price_card())
    assert path.is_file()
    assert ledger.snapshot()['committed_cny'] == '0'
    assert ledger.snapshot()['reservations'] == {}
    reopened = BudgetLedger(path, limit_cny='5', price_card=price_card())
    assert reopened.snapshot()['price_card'] == ledger.snapshot()['price_card']


@pytest.mark.parametrize('limit', ['0', '6'])
def test_invalid_budget_is_rejected_before_creating_a_runtime_directory(tmp_path, limit):
    path = tmp_path / 'invalid-run' / 'budget' / 'ledger.json'
    with pytest.raises(ValueError, match='BUDGET_LIMIT_OUT_OF_RANGE'):
        BudgetLedger(path, limit_cny=limit, price_card=price_card())
    assert not path.parent.exists()
