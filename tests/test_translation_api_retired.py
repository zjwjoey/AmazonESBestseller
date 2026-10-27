"""Retired transports cannot send requests, even with credentials configured."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller import cli
from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider
from amazon_es_bestseller.translation.ds import _default_transport, TranslationError
from amazon_es_bestseller.commands.translation_production import _service


def test_qwen_default_transport_cannot_send_and_has_no_retry():
    result = QwenMTProvider(api_key='offline-test', rate=0).translate('hola', asin='B000000001', field='title_es')
    assert result.status == 'failed'
    assert 'TRANSLATION_API_REMOVED' in result.error
    assert result.attempts == 0


def test_legacy_transport_is_removed():
    with pytest.raises(TranslationError, match='TRANSLATION_API_REMOVED') as caught:
        _default_transport('https://example.invalid', {}, {}, 1)
    assert not caught.value.retryable


def test_retired_ds_cli_rejects_before_input_or_confirmation(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda *_: pytest.fail('must not prompt'))
    with pytest.raises(SystemExit, match='TRANSLATION_API_REMOVED'):
        cli.cmd_translate_ds(SimpleNamespace())


def test_production_real_provider_rejects_before_ledger_or_cache(tmp_path):
    with pytest.raises(SystemExit, match='TRANSLATION_API_REMOVED'):
        _service(tmp_path, {'provider_mode': 'qwen-mt'}, SimpleNamespace())
    assert not list(tmp_path.iterdir())


def test_translation_package_has_no_network_client_imports():
    root = Path(__file__).parents[1] / 'src/amazon_es_bestseller/translation'
    forbidden = ('requests', 'httpx', 'openai', 'dashscope', 'urllib.request', 'urllib.error', 'http.client')
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ''] + [(node.module or '') + '.' + item.name for item in node.names]
            else:
                continue
            assert not any(name == item or name.startswith(item + '.') for name in names for item in forbidden), path
