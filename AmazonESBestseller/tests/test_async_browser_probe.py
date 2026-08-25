from amazon_es_bestseller.async_browser_probe import batch_targets


def test_batch_targets_limits_each_parallel_batch_to_requested_workers():
    batches = batch_targets(["one", "two", "three", "four", "five"], workers=2)

    assert batches == [["one", "two"], ["three", "four"], ["five"]]
