# Field Closure Audit implementation plan

1. Add a read-only field evidence-chain auditor with deterministic JSON/Markdown output and optional saved-HTML source checks.
2. Add only high-confidence pipeline/QA fixes: reliable attribute brand fallback and invalid original-price suppression with explicit QA code.
3. Add offline regression fixtures/tests for the four closure classifications, rank separation, unknown attributes, notes preservation, and CLI output.
4. Update the seven project MD files without changing the frozen three-sheet/26-column export contract.
5. Run the offline CLI chain, full pytest, diff checks, commit and push the feature branch when origin permits.
