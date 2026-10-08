from copy import deepcopy

import pytest

from amazon_es_bestseller.translation import legacy_reference as legacy
from amazon_es_bestseller.translation.cache import TranslationCache
from test_legacy_verified_review_input import _fixture


def evidence(tmp_path):
    source, candidates, kwargs = _fixture(tmp_path)
    review = legacy.prepare_legacy_review_input(candidates, **kwargs)
    row = review['candidates'][0]
    policy = {'policy_version': legacy.LEGACY_REVIEWED_POLICY_VERSION, 'semantic_decisions': [{
        'candidate_hash': row['candidate_hash'], 'source_hash': row['source_hash'],
        'context_hash': row['context_hash'], 'reviewed_value': row['candidate_value'],
        'decision': 'KEEP', 'semantic_review_status': 'PASS', 'review_model': 'CODEX',
        'review_note': 'Agent checked exact type and facts; not human gold.'}]}
    return source, candidates, kwargs, policy


def test_real_gate_dry_admission_then_independent_reference_cache(tmp_path):
    source, candidates, kwargs, policy = evidence(tmp_path)
    output = tmp_path / 'independent' / 'legacy_reference_cache.json'
    dry = legacy.admit_legacy_run_reference_cache(candidates, source['source_gate'], policy,
        output_path=output, dry_run=True, **kwargs)
    assert dry['admitted_fields'] == 1 and not output.exists()
    applied = legacy.admit_legacy_run_reference_cache(candidates, source['source_gate'], policy,
        output_path=output, **kwargs)
    cache = TranslationCache(output)
    assert len(cache.entries) == 1 and not cache.memory and not cache.memory_results
    envelope = next(iter(cache.entries.values()))
    assert envelope['provider'] == 'unknown' and envelope['source_kind'] == 'legacy_excel_reference'
    assert envelope['review_origin'] == 'agent' and not envelope['human_gold']
    assert envelope['resolution_source'] == 'legacy-reviewed-reference'
    assert envelope['attempt_count'] == 0 and applied['provider_calls'] == 0
    assert 'qwen' not in next(iter(cache.entries))
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        legacy.admit_legacy_run_reference_cache(candidates, source['source_gate'], policy, output_path=output, **kwargs)
    assert output.read_bytes() == before


@pytest.mark.parametrize('mutation', ['source_hash', 'context_hash', 'reviewed_value', 'semantic_review_status', 'review_model'])
def test_invalid_agent_admission_cannot_write_any_cache(tmp_path, mutation):
    source, candidates, kwargs, policy = evidence(tmp_path)
    bad = deepcopy(policy)
    bad['semantic_decisions'][0][mutation] = 'stale'
    output = tmp_path / 'not_written.json'
    with pytest.raises(ValueError, match='LEGACY_REQUESTED_ADMISSION_NOT_VERIFIED'):
        legacy.admit_legacy_run_reference_cache(candidates, source['source_gate'], bad, output_path=output, **kwargs)
    assert not output.exists()


def test_no_semantic_decision_stays_held_without_cache(tmp_path):
    source, candidates, kwargs, _ = evidence(tmp_path)
    output = tmp_path / 'not_written.json'
    report = legacy.admit_legacy_run_reference_cache(candidates, source['source_gate'],
        {'policy_version': legacy.LEGACY_REVIEWED_POLICY_VERSION, 'semantic_decisions': []},
        output_path=output, dry_run=True, **kwargs)
    assert report['admitted_fields'] == 0 and not output.exists()


def test_blocked_gate_cannot_be_called_verified_even_with_no_decisions(tmp_path):
    source, candidates, kwargs, _ = evidence(tmp_path)
    output = tmp_path / 'not_written.json'
    with pytest.raises(ValueError, match='LEGACY_FORMAL_GATE_BLOCKED'):
        legacy.admit_legacy_run_reference_cache(candidates, {**source['source_gate'], 'status': 'BLOCKED'},
            {'policy_version': legacy.LEGACY_REVIEWED_POLICY_VERSION, 'semantic_decisions': []},
            output_path=output, dry_run=True, **kwargs)
    assert not output.exists()
