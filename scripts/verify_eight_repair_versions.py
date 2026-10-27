"""Run the same eight offline regressions against main and runtime imports.

No production file is written. Test fixtures are copied to the output folder;
pytest runs there with an explicit source path and independent configuration.
"""
from __future__ import annotations

import argparse
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


def verify(output):
    checkout = Path(__file__).resolve().parents[1]
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
             'task_runtime': Path(r'F:\AmazonESBestseller\.releases\action5500-main6c1cf342-compat\src'),
             'repair_worktree': checkout / 'src'}
    results = {'remote_main': main, 'versions': {}}
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
    verify(parser.parse_args().output)
