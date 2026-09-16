$ErrorActionPreference = "Stop"
Set-Location "F:\AmazonESBestseller"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONPATH = "src;."

# One-click resumable runner for the final 4,500-SKU completion phase.
# Categories run serially and continue directly when the previous category
# completes normally. Access restrictions still stop the run immediately.
python -m amazon_es_bestseller.cli batch-collect `
  --plan configs/amazon_es_4500_rank_31_50_completion_plan.json `
  --out-dir outputs/scale_4500_repair/rank_31_50_batch `
  --headful `
     --manual-assist `
     --postal-code 28001 `
     --existing-products outputs/scale_4500_repair/trace_enriched/products_4500_trace.json `
     --seed-rankings outputs/scale_4500_repair/trace_enriched/ranking_supplement_all_categories_31_50.json

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
