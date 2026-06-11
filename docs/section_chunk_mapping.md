# Section-to-Chunk Mapping

How the eval harness resolves `source_sections` references from `qa_pairs.json` to retrievable chunks across the four Knowledge Base configurations.

## Source Section Reference Formats

Each Q&A pair in `qa_pairs.json` has a `source_sections` array containing one or more identifiers. There are two formats:

**FCA section IDs** follow the pattern `{module}{chapter}s{section}`, e.g. `sysc10s1`, `prin2as1`, `cobs4s2`. These correspond directly to filenames in `data/fca/sections/` (whole-section markdown) and to the `section` metadata field on all FCA-derived chunks.

**Synthetic policy slugs** follow the pattern `synthetic_{slug}`, e.g. `synthetic_conflicts-of-interest-policy`. These map to the policy file at `data/synthetic/policies/{slug}.md`.

## Mapping by KB Type

### Structure-Aware KBs (NONE chunking)

KBs: `structure-titan`, `structure-cohere`

These KBs ingest pre-chunked files from `data/fca/sections-structure/` and `data/synthetic/policies/`. Bedrock performs no additional chunking (`NONE` strategy). Each chunk has its own metadata sidecar.

**FCA sections:** Every structure-aware chunk inherits a `section` metadata field from its parent section. Chunk filenames follow the pattern `{section_id}-{chunk_index:03d}.md`, e.g. `sysc10s1-000.md`, `sysc10s1-001.md`. To find all chunks belonging to a source section, the eval harness should use the Bedrock KB `Retrieve` API with a metadata filter:

```python
filter = {
    "equals": {
        "key": "section",
        "value": "sysc10s1"  # from source_sections
    }
}
```

This returns all chunk URIs for that section. A single FCA section typically maps to 1–N chunks (median ~3, max ~90 for large sections like `mcob9s4`).

**Synthetic policies:** Synthetic policy files are ingested as whole documents (no sub-chunking). The metadata sidecar has `document_title` (e.g. "Conflicts of Interest Policy") but no `section` field. The eval harness maps `synthetic_{slug}` to the S3 key: `s3://rag-eval-data-{account}/synthetic/policies/{slug}.md`. Since each policy is a single document, the harness can filter by `source` metadata:

```python
filter = {
    "andAll": [
        {"equals": {"key": "source", "value": "synthetic"}},
        {"equals": {"key": "document_title", "value": "Conflicts of Interest Policy"}}
    ]
}
```

Alternatively, the harness can match on the S3 URI returned in retrieval results.

### Fixed-Size KBs (FIXED_SIZE chunking)

KBs: `fixed-titan`, `fixed-cohere`

These KBs ingest whole-section files from `data/fca/sections/` and whole-policy files from `data/synthetic/policies/`. Bedrock re-chunks them server-side using a fixed-size strategy. The metadata sidecar is attached at the source-file level, so all chunks derived from a given source file inherit that file's metadata.

**FCA sections:** Each whole-section file has a `section` metadata field (e.g. `"section": "sysc10s1"`). Bedrock's server-side chunks inherit this field. The eval harness uses the same metadata filter as the structure-aware KBs:

```python
filter = {
    "equals": {
        "key": "section",
        "value": "sysc10s1"
    }
}
```

The number of returned chunks will differ from the structure-aware KBs because Bedrock's fixed-size splitter produces different chunk boundaries. This is expected and is one of the dimensions the eval framework compares.

**Synthetic policies:** Same approach as structure-aware KBs — filter by `source` + `document_title`, or match on S3 URI. The metadata sidecar on the whole-policy file is inherited by all Bedrock-generated chunks.

## Slug-to-Title Lookup

For `policy-regulation` type Q&A pairs, the eval harness needs to map `synthetic_{slug}` to a `document_title` for metadata filtering. The canonical mapping is derived from the policy filenames and their metadata sidecars:

| Slug (from `source_sections`) | `document_title` (metadata field) |
|---|---|
| `synthetic_conflicts-of-interest-policy` | Conflicts of Interest Policy |
| `synthetic_client-money-handling-procedures` | Client Money Handling Procedures |
| `synthetic_data-protection-compliance-framework` | Data Protection Compliance Framework |
| `synthetic_financial-promotions-compliance-manual` | Financial Promotions Compliance Manual |
| `synthetic_operational-resilience-framework` | Operational Resilience Framework |
| ... | *(derive from sidecar at runtime)* |

The harness should build this lookup dynamically by reading `data/synthetic/policies/*.md.metadata.json` at startup rather than hardcoding it, since new policies may be added.

## Eval Harness Implementation Notes

The eval harness (JAM-242) should implement a `resolve_source_chunks(source_section_id, kb_config)` function that:

1. Determines corpus type from the ID format: starts with `synthetic_` → synthetic policy; otherwise → FCA section.
2. Builds the appropriate metadata filter for the target KB.
3. Calls the Bedrock KB `Retrieve` API with a representative query and the metadata filter to enumerate matching chunks.
4. Returns the set of chunk URIs that constitute the ground-truth "expected retrieval set" for that Q&A pair.

This expected retrieval set is then compared against the chunks actually returned by each KB's retrieval to compute recall, precision, and other retrieval quality metrics.

## Key Pitfall

The `AMAZON_BEDROCK_TEXT` metadata field type was declared non-filterable during KB creation (JAM-240 constraint: `metadata_configuration` is immutable after creation). If `section` was registered as non-filterable on any KB, the metadata filter approach will fail silently (returning zero results). Verify filterability by testing a simple retrieve-with-filter call against each KB before running the full eval. If needed, the KBs must be recreated with `section` declared as filterable — this was accounted for in the JAM-240 setup.
