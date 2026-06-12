# ADR-001: S3 Vectors as the Vector Store

## Status
Accepted

## Context
Bedrock Knowledge Bases support multiple vector store backends: Amazon OpenSearch Serverless, Aurora PostgreSQL (pgvector), MongoDB Atlas, Pinecone, Redis Enterprise, and Amazon S3 Vectors. The project needs a managed vector store that minimises operational overhead for a portfolio project.

## Decision
Use Amazon S3 Vectors as the vector store backend for all four Knowledge Base configurations.

## Rationale
S3 Vectors is fully managed with zero infrastructure provisioning — no cluster sizing, no index tuning, no capacity planning. For a project focused on evaluating chunking and embedding strategies rather than vector store performance, operational simplicity is the priority.

The alternative — OpenSearch Serverless — would have enabled hybrid search (dense + BM25 via Reciprocal Rank Fusion), expanding the config matrix from 4 to 8. However, it requires provisioning OCU capacity, managing collection policies, and monitoring index health — overhead that doesn't serve the project's evaluation goals.

## Consequences
- HYBRID search is unavailable. Passing `overrideSearchType: "HYBRID"` to the Retrieve API throws a `ValidationException` on S3 Vectors-backed KBs.
- The configuration matrix is 4 (2 chunking × 2 embedding × 1 retrieval) instead of the originally planned 8.
- Migrating to OpenSearch Serverless in future would require recreating all four KBs but no code changes — the pipeline's `search_type` parameter already supports both values.
