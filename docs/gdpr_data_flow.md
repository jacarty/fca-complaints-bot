# GDPR Data-Flow — FCA Complaints Handling Bot

**Scope.** This documents the as-built data flow for a single `/ask` turn and
classifies the personal data at each hop. It is a demonstration deployment: the
diagram records what the system does today and flags the gaps a production
deployment would need to close. The full framework-by-framework control mapping
(GDPR Art 30, lawful basis, retention, FCA/PRA, DORA, EU AI Act) lives in the
compliance mapping (deliverable 4); this doc stays at the level of *what data
goes where, in which region, and where it is protected*.

**Roles.** The deploying firm is the **data controller**; Amazon Bedrock is a
**processor** (Art 28). The data subject is the complainant whose details the
handler enters.

---

## Data flow (one `/ask` turn)

![GDPR data flow for one /ask turn](assets/gdpr_data_flow.svg)

**Legend** — colour denotes component type: grey = handler/browser, teal =
application (eu-west-1), blue = Amazon Bedrock (Art 28 processor, ZDR). The amber
outline marks the **Vault**, the re-identification key.

The load-bearing property is structural, not colour-coded: raw personal data
crosses the **pseudonymisation boundary** at `redact()` before it reaches any
processor, and is only re-identified at `rehydrate()`, inside the controller's
boundary, for the authorised handler. Everything the model, the conversation
history, and the audit trail hold is pseudonymised — the edge labels (raw PII →
pseudonymised → tokenised → real values) trace where each classification applies.

---

## Per-hop annotations

| Component / hop | Data class | Residency | Control / note | Known gap |
|---|---|---|---|---|
| Handler input | Personal | client → eu-west-1 | Ingress of the complainant's data | No authentication (single-user demo); TLS assumed for production, not the local dev server |
| `redact()` boundary | → Pseudonymised | eu-west-1 | Pseudonymisation (Art 4(5)) before any egress; measured **96% recall / 64% precision** | Regex NER misses lowercase/untitled names and over-masks orgs/public reference data; production path is Comprehend / Bedrock Guardrails |
| Session + Vault | Personal (re-id key) | eu-west-1, in-memory | The token↔value map — highest-value secret; never sent to Bedrock or written to the audit log | In-memory, unencrypted, no TTL or access control; lost on restart |
| Bedrock generation | Pseudonymised in | eu-west-1 | Art 28 processor; **ZDR** (no retention or training on inputs); the `eu.` profile is EU-pinned by the profile itself | — (EU-pinned regardless of `AWS_REGION`; see residency controls below) |
| KB retrieval | No personal data | eu-west-1 (verified) | FCA Handbook (public); Titan embed v2 + S3 Vectors index `arn:aws:s3vectors:eu-west-1:982099554067:…/fca-complaints-structure-titan` — endpoint, KB, vector store all in-region | Residency follows the `AWS_REGION` config, not a hard pin — see residency controls below |
| `redact()` on output | Pseudonymised | eu-west-1 | Catches model-emitted PII; render-safe over-masking of public reference data (e.g. the FOS contact block) | Same regex limitation as ingress |
| Conversation history | Pseudonymised at rest | eu-west-1, in-memory | The model only ever re-sees tokens on later turns | In-memory, volatile |
| Audit trail | Pseudonymised personal data | eu-west-1, local JSONL | HMAC-keyed hash of the raw input (not the text), masked output, detection counts; append-only | Still GDPR-scoped (retention, access control, DSAR/erasure apply); **fail-open** write; local file (production: encrypted, access-controlled store); hand-editing breaks the append-only property |
| `rehydrate()` | → Personal | eu-west-1 | Re-identification happens outside the model boundary, for the authorised handler only | Relies entirely on Vault confidentiality |
| Handler render | Personal | eu-west-1 → client | Authorised viewer sees real values | No authentication; TLS assumed for production |

---

## Residency controls

The path stays in the EU via two distinct mechanisms — worth separating, because
they fail differently:

- **Generation is profile-pinned.** `eu.anthropic.claude-sonnet-4-6` is an EU
  regional inference profile, so inference is served from EU regions. This is a
  property of the profile, not of `AWS_REGION`; the failure mode is reverting to
  a `global.` profile (which may route worldwide), as the earlier configuration
  did.
- **Retrieval is resource-pinned.** The Knowledge Base, the
  `titan-embed-text-v2:0` endpoint, and the S3 Vectors index physically reside in
  eu-west-1 (per the index ARN). Retrieval cannot leave the region without
  re-provisioning those resources.
- **`AWS_REGION` is the config that must agree with both.** It governs which
  regional endpoints the application calls and should be `eu-west-1`. It is
  configuration, not a hard guarantee — a misconfigured region is the residual
  residency risk to monitor.

---

## Known gaps (carried to the compliance mapping)

- **In-memory Vault and session** — volatile, unencrypted, no TTL or access
  control. The Vault is the re-identification key and the single highest-value
  asset; production requires encrypted, access-controlled, expiring storage.
- **No authentication** — single-user demo keyed only by a session cookie; no
  per-handler identity or authorisation.
- **Fail-open audit** — a write failure logs but does not block the response.
  A regulated deployment may choose fail-closed (withhold the answer if it
  cannot be audited).
- **Redaction precision** — 96% recall / 64% precision; over-masks
  organisations and public reference data, and misses lowercase/untitled names.
  Entity-typed detection (Comprehend / Guardrails) is the production path.
- **Transport security** — TLS is assumed for any non-local deployment; the dev
  server runs over plain HTTP on localhost.

---

*Cross-reference: full GDPR / FCA-PRA / DORA / EU AI Act control mapping →*
`docs/compliance_mapping.md` *(deliverable 4).*
