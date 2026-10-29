"""Historical replay and file-journal queries cannot modify their source."""
import pytest
from amazon_es_bestseller.cli import build_parser
from amazon_es_bestseller.highspeed.v3_workflows import readonly_store
from amazon_es_bestseller.multisource.cli import dispatch
from amazon_es_bestseller.multisource.identity import ProductIdentityRegistry
from test_highspeed_readonly_cli import fingerprint
from test_highspeed_cli import offline_fixture, invoke
from test_multisource_snapshots import setup
from test_multisource_identity import confirmed


def test_historical_v3_store_reads_wal_copy_without_source_shm_changes(tmp_path):
    from amazon_es_bestseller.highspeed.store import RunStore
    path, _ = offline_fixture(tmp_path);root = tmp_path/'store'
    invoke('--offline','highspeed','run','--root',str(root),'--plan',str(path))
    writer = RunStore(root)
    try:
        writer.db.execute('CREATE TABLE test_only_wal(value INTEGER)')
        writer.db.execute('INSERT INTO test_only_wal VALUES(42)')
        before = fingerprint(root)
        with readonly_store(root) as reader:
            assert reader.db.execute('SELECT value FROM test_only_wal').fetchone()[0] == 42
        assert fingerprint(root) == before
    finally:writer.close()


@pytest.mark.parametrize('operation', ['status','qa','compare','report','exceptions'])
def test_multisource_query_missing_root_does_not_create_it(tmp_path, operation):
    root = tmp_path/'absent'
    args = build_parser().parse_args(['--offline','multisource',operation,'--root',str(root),'--run-id','TEST_ONLY'])
    with pytest.raises(ValueError,match='EXISTING_MULTISOURCE'):
        dispatch(args)
    assert not root.exists()


def test_multisource_status_and_qa_keep_registry_identity_and_checkpoints_unchanged(tmp_path):
    manager, source, plan, pages = setup(tmp_path)
    manager.run(plan,[source],run_id='TEST_ONLY',pages=pages)
    before = fingerprint(manager.root)
    for operation in ('status','qa'):
        args = build_parser().parse_args(['--offline','multisource',operation,'--root',str(manager.root),'--run-id','TEST_ONLY'])
        dispatch(args)
        assert fingerprint(manager.root) == before


def test_identity_query_projects_current_evidence_without_publishing_views(tmp_path):
    row = confirmed(tmp_path)
    directory = tmp_path/'identity'
    registry = ProductIdentityRegistry(directory)
    registry.ingest(row)
    before = fingerprint(directory)
    changed = {**row,'title_raw':'changed non-identity display field'}
    reader = ProductIdentityRegistry(directory,create=False)
    view = reader.materialize_views([changed],'v2',persist=False)
    assert view[0]['observation_id'] == row['observation_id']
    assert fingerprint(directory) == before
