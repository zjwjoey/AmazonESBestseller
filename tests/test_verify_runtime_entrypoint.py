"""Version auditing must follow the installed task, without importing its runner."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest


def verifier():
    path = Path(__file__).parents[1] / 'scripts/verify_eight_repair_versions.py'
    spec = importlib.util.spec_from_file_location('runtime_version_verifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task_tree(tmp_path, release_expression):
    project = tmp_path / 'project'
    task = project / 'runtime/task'
    task.mkdir(parents=True)
    (task / 'run.py').write_text(
        'from pathlib import Path\nROOT = Path(__file__).resolve().parent\n'
        'PROJECT = ROOT.parents[1]\nRELEASE = ' + release_expression + '\n'
        'raise RuntimeError("AUDIT_MUST_NEVER_IMPORT_RUNNER")\n', encoding='utf-8')
    return project, task


def test_matrix_uses_installed_entrypoint_not_archived_release(tmp_path, monkeypatch):
    module = verifier()
    project, task = task_tree(tmp_path, 'PROJECT')
    (project / 'src').mkdir()
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    for folder in ['html/remaining_review_20261024', 'translation']:
        (checkout / 'tests/fixtures' / folder).mkdir(parents=True)
    monkeypatch.setattr(module, '__file__', str(checkout / 'scripts/verify.py'))
    monkeypatch.setattr(module, 'TESTS', [])
    monkeypatch.setattr(module, 'DEFAULT_TASK_ROOT', task, raising=False)

    def check_output(command, **kwargs):
        if command[0] == 'git':
            return 'abc\trefs/heads/main\n' if 'ls-remote' in command else 'abc\n'
        return str(Path(kwargs['env']['PYTHONPATH']).resolve() / 'amazon_es_bestseller/__init__.py')

    def run(command, **kwargs):
        if command[0] == 'git':
            with zipfile.ZipFile(command[command.index('-o') + 1], 'w') as archive:
                archive.writestr('src/amazon_es_bestseller/__init__.py', '')
        else:
            receipt = next(value.split('=', 1)[1] for value in command if value.startswith('--junitxml='))
            Path(receipt).write_text('<testsuites/>', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(module.subprocess, 'check_output', check_output)
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.verify(tmp_path / 'output')
    assert Path(result['versions']['task_runtime']['source_root']).resolve() == project / 'src'


@pytest.mark.parametrize('mode', ['legacy', 'literal', 'project'])
def test_resolves_supported_release_without_executing_task(tmp_path, mode):
    module = verifier()
    project = tmp_path / 'project'
    release = project / '.releases/old' if mode == 'legacy' else project
    expression = {'legacy': "PROJECT / '.releases/old'", 'literal': f'Path({str(project)!r})', 'project': 'PROJECT'}[mode]
    _, task = task_tree(tmp_path, expression)
    (release / 'src').mkdir(parents=True)
    assert module.task_source_root(task) == release / 'src'


@pytest.mark.parametrize('expression', ['choose_release()', "Path('missing-release')"])
def test_unknown_or_missing_release_cannot_fall_back_to_old_package(tmp_path, expression):
    module = verifier()
    _, task = task_tree(tmp_path, expression)
    with pytest.raises(ValueError, match='TASK_RELEASE'):
        module.task_source_root(task)
