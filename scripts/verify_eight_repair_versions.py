"""Run the same eight offline regressions against main and runtime imports.

No production file is written. Test fixtures are copied to the output folder;
pytest runs there with an explicit source path and independent configuration.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

TESTS = [
    'test_specification_mixed_axis_units.py', 'test_translation_material_exclusion_label.py',
    'test_translation_mixed_axis_validator.py', 'test_field_closure_identity_residual.py',
    'test_specification_dimension_pack_boundary.py', 'test_specification_part_number_count.py',
    'test_translation_dimension_axis_display.py', 'test_translation_water_resistance.py',
]
DEFAULT_TASK_ROOT = Path(r'F:\AmazonESBestseller\runtime\action_detail5500_live_20261023_161301_8b4e')


def task_source_root(task_root):
    """Read the two reviewed release forms without importing/executing a runner."""
    task_root = Path(task_root).resolve()
    try:
        tree = ast.parse((task_root / 'run.py').read_text(encoding='utf-8'))
    except (OSError, SyntaxError) as error:
        raise ValueError('TASK_RELEASE_ENTRYPOINT_UNREADABLE') from error
    assignments = {node.targets[0].id: node.value for node in tree.body
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)}

    def literal_path(node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'Path' and len(node.args) == 1 and not node.keywords
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            path = Path(node.args[0].value)
            if path.is_absolute():
                return path.resolve()
        return None

    project_expression = assignments.get('PROJECT')
    project = literal_path(project_expression)
    if project is None and project_expression is not None:
        if ast.unparse(project_expression) == 'ROOT.parents[1]':
            project = task_root.parents[1]
    release_expression = assignments.get('RELEASE')
    release = literal_path(release_expression)
    if release is None and project is not None:
        if isinstance(release_expression, ast.Name) and release_expression.id == 'PROJECT':
            release = project
        elif (isinstance(release_expression, ast.BinOp) and isinstance(release_expression.op, ast.Div)
              and isinstance(release_expression.left, ast.Name) and release_expression.left.id == 'PROJECT'
              and isinstance(release_expression.right, ast.Constant) and isinstance(release_expression.right.value, str)):
            release = (project / release_expression.right.value).resolve()
    if release is None or not (release / 'src').is_dir():
        raise ValueError('TASK_RELEASE_UNSUPPORTED_OR_MISSING')
    return release / 'src'


def verify(output, task_root=None):
    checkout = Path(__file__).resolve().parents[1]
    task_root = Path(task_root or DEFAULT_TASK_ROOT).resolve()
    task_source = task_source_root(task_root)
    output = Path(output).resolve()
    if output.is_relative_to(Path(r'F:\AmazonESBestseller')):
        raise ValueError('PRODUCTION_OUTPUT_FORBIDDEN')
    output.mkdir(parents=True, exist_ok=False)
    tests = output / 'tests'
    tests.mkdir()
    for name in TESTS:
        shutil.copy2(checkout / 'tests' / name, tests / name)
    for folder in ['html/remaining_review_20261024', 'translation']:
        shutil.copytree(checkout / 'tests/fixtures' / folder, tests / 'fixtures' / folder)
    config = output / 'pytest.ini'
    config.write_text('[pytest]\naddopts =\n', encoding='utf-8')
    main = subprocess.check_output(['git', 'rev-parse', 'origin/main'], cwd=checkout, text=True).strip()
    remote = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/main'], cwd=checkout, text=True).split()[0]
    if main != remote:
        raise ValueError('REFRESH_MAIN_BEFORE_VERIFYING')
    archive = output / 'main.zip'
    subprocess.run(['git', 'archive', '--format=zip', '-o', str(archive), main, 'src'], cwd=checkout, check=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(output / 'main')
    roots = {'origin_main': output / 'main/src',
             'formal_runtime': Path(r'F:\AmazonESBestseller\src'),
             'task_runtime': task_source,
             'repair_worktree': checkout / 'src'}
    results = {'remote_main': main, 'task_entrypoint': str(task_root / 'run.py'),
               'task_entrypoint_sha256': hashlib.sha256((task_root / 'run.py').read_bytes()).hexdigest(),
               'versions': {}}
    for label, root in roots.items():
        print('Verifying ' + label, flush=True)
        env = {**os.environ, 'PYTHONPATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
        command = [sys.executable, '-B', '-m', 'pytest', '-q', '-c', str(config),
                   '--import-mode=importlib', '--junitxml=' + str(output / (label + '.xml'))]
        command += [str(tests / name) for name in TESTS]
        result = subprocess.run(command, cwd=output, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
        (output / (label + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        tree = ET.parse(output / (label + '.xml'))
        cases = tree.findall('.//testcase')
        by_group = {}
        for name in TESTS:
            group = [case for case in cases if case.get('classname', '').endswith(name.removesuffix('.py'))]
            by_group[name] = {'total': len(group), 'passed': sum(not any(case.find(x) is not None for x in ['failure', 'error', 'skipped']) for case in group),
                              'failed': sum(case.find('failure') is not None or case.find('error') is not None for case in group)}
        imported = subprocess.check_output([sys.executable, '-B', '-c',
            'import amazon_es_bestseller;print(amazon_es_bestseller.__file__)'], cwd=output, env=env, text=True).strip()
        if not Path(imported).resolve().is_relative_to(root.resolve()):
            raise ValueError('WRONG_IMPORT:' + imported)
        results['versions'][label] = {'source_root': str(root), 'imported_module': imported, 'exit_code': result.returncode,
            'groups': by_group, 'source_hashes': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root / 'amazon_es_bestseller').rglob('*') if path.suffix in {'.py', '.json'}}}
        print(label, {name: f"{group['passed']}/{group['total']}" for name, group in by_group.items()}, flush=True)
    (output / 'version_matrix.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-root', type=Path, default=DEFAULT_TASK_ROOT)
    args = parser.parse_args()
    verify(args.output, args.task_root)
