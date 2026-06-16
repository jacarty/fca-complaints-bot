# Drafting Evaluation Results

Generated: 2026-06-15 18:06 UTC

## Configuration Comparison

| Config | Coverage | Section P | Citation P/R | Retr. Recall | Gap | Grounded % | Agreement |
|--------|----------|-----------|--------------|--------------|-----|------------|-----------|
| fixed-titan | 52.8% | 9.6% | 21.4% / 23.7% | 11.4% | -12.4% | 72.0% | 81.7% |
| structure-titan | 55.2% | 14.0% | 30.7% / 33.1% | 32.6% | -0.5% | 82.3% | 83.9% |

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