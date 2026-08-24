from pathlib import Path


def test_project_files_are_isolated_under_amazon_es_bestseller():
    repo_root = Path(__file__).resolve().parents[2]
    project_root = repo_root / "AmazonESBestseller"
    assert (project_root / "pyproject.toml").is_file()
    assert (project_root / "src").is_dir()
    assert (project_root / "tests").is_dir()
    assert not (repo_root / "pyproject.toml").exists()
    assert not (repo_root / "src").exists()
    assert not (repo_root / "config").exists()
