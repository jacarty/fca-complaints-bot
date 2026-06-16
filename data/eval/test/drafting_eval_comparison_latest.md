# Drafting Evaluation Results

Generated: 2026-06-15 10:41 UTC

## Configuration Comparison

| Config | Coverage | Section P | Citation P/R | Retr. Recall | Gap | Grounded % | Agreement |
|--------|----------|-----------|--------------|--------------|-----|------------|-----------|
| fixed-titan | 53.3% | 9.6% | 23.2% / 25.3% | 0.0% | -25.3% | 73.4% | 83.1% |
| structure-titan | 56.5% | 14.0% | 30.9% / 31.9% | 32.6% | +0.7% | 80.0% | 83.6% |

*Based on 35 scenarios per config*

## Metric Definitions

- **Coverage**: Weighted rubric coverage (PRESENT 1.0, PARTIAL 0.5) over each scenario's required elements (primary judge)
- **Section P**: Fraction of retrieved chunks whose section is an expected section (the KB comparison)
- **Citation P/R**: Of provisions the bot cited, fraction expected / of expected provisions, fraction cited (normalised)
- **Retr. Recall**: Fraction of expected provisions whose rule text was actually retrieved (present as a header)
- **Gap**: Retrieved-text recall minus citation recall. Positive = rule retrieved but not cited (drafting miss); near-zero with low recall = KB miss
- **Grounded %**: Regulatory claims grounded in retrieved provisions (grounding judge, overall)
- **Agreement**: Element-level agreement between the two rubric judges

Per-field grounding (handler vs customer draft) is in the aggregate JSON; a low customer-draft figure against a high handler figure is the drift signal.