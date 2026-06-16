# Drafting Evaluation Results

Generated: 2026-06-14 18:58 UTC

## Configuration Comparison

| Config | Coverage | Section P | Citation P/R | Retr. Recall | Gap | Grounded % | Agreement |
|--------|----------|-----------|--------------|--------------|-----|------------|-----------|
| fixed-titan | 61.9% | 6.2% | 25.0% / 33.3% | 0.0% | -33.3% | 66.4% | 61.9% |
| structure-titan | 58.3% | 15.0% | 53.6% / 56.7% | 66.7% | +10.0% | 35.0% | 60.7% |

*Based on 2 scenarios per config*

## Metric Definitions

- **Coverage**: Weighted rubric coverage (PRESENT 1.0, PARTIAL 0.5) over each scenario's required elements (primary judge)
- **Section P**: Fraction of retrieved chunks whose section is an expected section (the KB comparison)
- **Citation P/R**: Of provisions the bot cited, fraction expected / of expected provisions, fraction cited (normalised)
- **Retr. Recall**: Fraction of expected provisions whose rule text was actually retrieved (present as a header)
- **Gap**: Retrieved-text recall minus citation recall. Positive = rule retrieved but not cited (drafting miss); near-zero with low recall = KB miss
- **Grounded %**: Regulatory claims grounded in retrieved provisions (grounding judge, overall)
- **Agreement**: Element-level agreement between the two rubric judges

Per-field grounding (handler vs customer draft) is in the aggregate JSON; a low customer-draft figure against a high handler figure is the drift signal.