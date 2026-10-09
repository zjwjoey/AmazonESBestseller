import json

from amazon_es_bestseller.translation.run_context import TranslationRunContext
from test_translation_run_context import setup_run, save


def test_bound_history_missing_from_live_cache_is_visible_without_dryrun_write(tmp_path):
    products, config, manifest, rows = setup_run(tmp_path)
    reference = json.loads(config.read_text(encoding='utf-8'))['run_context']
    context = TranslationRunContext(reference, products_path=products, products=rows, cache_path=manifest['run_cache_path'])
    context.prepare_execution()
    data = json.loads(context.cache.path.read_text(encoding='utf-8'))
    historical = dict(data['memory'])
    data['memory'] = {}
    save(context.cache.path, data)
    before = context.cache.path.read_bytes()
    marker = context.marker.read_bytes()
    resumed = TranslationRunContext(reference, products_path=products, products=rows, cache_path=manifest['run_cache_path'])
    assert resumed.cache.memory == historical
    assert context.cache.path.read_bytes() == before
    resumed.prepare_execution()
    assert json.loads(context.cache.path.read_text(encoding='utf-8'))['memory'] == historical
    assert context.marker.read_bytes() == marker


def test_bound_history_never_overwrites_existing_live_candidate(tmp_path):
    products, config, manifest, rows = setup_run(tmp_path)
    reference = json.loads(config.read_text(encoding='utf-8'))['run_context']
    context = TranslationRunContext(reference, products_path=products, products=rows, cache_path=manifest['run_cache_path'])
    context.prepare_execution()
    data = json.loads(context.cache.path.read_text(encoding='utf-8'))
    next(iter(data['memory'].values()))['candidate_text'] = 'existing failed candidate'
    next(iter(data['memory'].values()))['qa_status'] = 'qa_failed'
    save(context.cache.path, data)
    resumed = TranslationRunContext(reference, products_path=products, products=rows, cache_path=manifest['run_cache_path'])
    resumed.prepare_execution()
    assert json.loads(context.cache.path.read_text(encoding='utf-8')) == data
