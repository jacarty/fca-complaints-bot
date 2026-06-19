# Compliance Mapping — FCA Complaints Handling Bot

**Scope.** This maps the security and data-protection controls in this repository
to the obligations of four frameworks: UK/EU **GDPR**, **FCA/PRA** handbook
rules, **DORA**, and the **EU AI Act**. It is an *engineering-level* mapping for a
**demonstration deployment**, not legal advice and not a formal compliance
attestation — a deploying firm's DPO and compliance function would own the
authoritative version. The deploying firm is the **data controller**; Amazon
Bedrock is an Art 28 **processor**.

The point of the document is honesty about coverage: it records what the system
*evidences today* and where a production deployment would have to close a gap.

**Status legend**

| Status | Meaning |
|---|---|
| **Addressed** | Demonstrated in the codebase / architecture as built |
| **Partial** | Mechanism present but with a known limitation or scope cut |
| **Gap** | Not implemented; a production deployment would need to add it |
| **Out of scope** | An organisational/legal obligation a demo repo cannot satisfy |

Cross-reference: the per-hop data flow and residency posture live in
[`gdpr_data_flow.md`](gdpr_data_flow.md); the redaction precision/recall numbers
come from the PII eval harness.

---

## 1. Summary matrix (control × framework)

| Control | GDPR | FCA/PRA | DORA | EU AI Act | Status |
|---|---|---|---|---|---|
| PII redaction / pseudonymisation | Art 4(5), 5(1)(c), 25 | SYSC data governance | ICT risk mgmt | — | **Partial** (96% recall / 64% precision; over-masks) |
| Reversible re-identification Vault | Art 4(5), 32 | SYSC 9 | Resilience | — | **Gap** (in-memory, unencrypted, no TTL) |
| Audit trail (HMAC, append-only JSONL) | Art 30 | SYSC 9 record-keeping | Logging | Record-keeping support | **Addressed** (app-level) |
| EU data residency | Art 44–49 transfers | SYSC 8 outsourcing | ICT 3rd-party | — | **Addressed** (profile- + resource-pinned) |
| Processor terms / ZDR | Art 28 | SYSC 8 | ICT 3rd-party | Provider documentation | **Addressed** (architectural); DPA out of scope |
| Human-in-the-loop review | — | DISP, SM&CR | — | Human oversight | **Addressed** |
| AI transparency / disclosure | — | Consumer Duty | — | Art 50 | **Partial** (handler-aware; customer disclosure is a deployment choice) |
| Regulatory grounding (DISP timing fences) | — | DISP 1.x, 2.8.2R | — | — | **Addressed** |
| Authentication / access control | Art 32 | SYSC | ICT risk mgmt | — | **Gap** (single-user demo) |
| Transport security (TLS) | Art 32 | SYSC | ICT risk mgmt | — | **Gap** (plain HTTP on localhost) |

The sections below give the reasoning behind each status.

---

## 2. GDPR

**Applicability.** The bot processes the personal data of complainants (the data
subjects). The firm is controller; Bedrock is an Art 28 processor. The
controlling design choice is that personal data is **pseudonymised before any
egress** to the processor and only re-identified inside the controller's boundary
for the authorised handler.

| Requirement | How the system addresses it | Status |
|---|---|---|
| Art 4(5) — Pseudonymisation | Reversible tokenisation at the `redact()` boundary; the model, history and audit log only ever hold tokens. Note this is *pseudonymisation, not anonymisation* — the Vault and audit log remain personal data | **Addressed** |
| Art 5(1)(c) — Data minimisation | The processor receives only pseudonymised text; raw PII is not transmitted | **Partial** (full input is processed transiently in-app before redaction) |
| Art 25 — Data protection by design & default | Pseudonymise-before-egress, output re-redaction (defence-in-depth) and a PII-free audit trail are built into the request path, not bolted on | **Partial** (undermined by the unencrypted Vault and absent auth) |
| Art 28 — Processor | Bedrock is used as a processor under ZDR (no retention or training on inputs) | **Addressed** architecturally; a signed DPA with AWS is a deployment task — **Out of scope** here |
| Art 30 — Records of processing | The append-only audit trail records each processing event: HMAC of the input, masked output, model ID, cited provisions, review flags | **Addressed** (app-level); a formal ROPA is **Out of scope** |
| Art 32 — Security of processing | EU-pinned in-region processing, keyed (HMAC) hashing of inputs, pseudonymisation, defence-in-depth redaction | **Partial** — Vault unencrypted, TLS assumed, audit writes fail-open |
| Art 44–49 — International transfers | Generation is EU-pinned by the `eu.` inference profile; retrieval resources are all eu-west-1; no third-country transfer | **Addressed** |
| Art 15–17 — DSAR / rectification / erasure | The audit log holds only pseudonymised data plus a keyed hash; the Vault is the sole re-identification key | **Gap** — no DSAR/erasure workflow; in-memory Vault is not durably addressable, and append-only audit complicates erasure |
| Art 35 — DPIA | — | **Gap** — processing complaint PII at scale would likely warrant a DPIA; the demo does not include one |

---

## 3. FCA / PRA

**Applicability.** A firm using this tool remains fully responsible for its
complaint handling under **DISP**, and for its systems and controls under
**SYSC**. The bot is an assistive drafting tool: a human handler reviews and owns
every response, which preserves the regulatory accountability chain.

| Requirement | How the system addresses it | Status |
|---|---|---|
| DISP — Complaint handling | The pipeline correctly fences the three timing concepts it must not get wrong: the eight-week firm response clock, the six-month FOS referral window, and the DISP 2.8.2R eligibility time-bar. It drafts; the handler decides | **Addressed** |
| SYSC 9 — Record-keeping | The audit trail provides a per-decision, tamper-evident (append-only, HMAC) record of what was asked, which provisions were cited, and what was drafted | **Addressed** (app-level) |
| SYSC 8 / 13 — Outsourcing & third-party | Bedrock/AWS is the outsourced ICT provider; processing is pinned in-region under ZDR | **Partial** — no formal outsourcing agreement, exit plan, or provider due-diligence in the demo |
| SYSC 3/4 — Systems & controls | Security controls (redaction, audit, residency) are demonstrated | **Partial** — not a full governance framework |
| Operational resilience (PS21/3) | — | **Gap** — single ICT provider, in-memory state lost on restart, no impact-tolerance or resilience testing |
| Consumer Duty | Grounded, provision-cited drafting supports clear, accurate customer communications | **Partial** — a tool that supports good outcomes, not a guarantee of them |
| SM&CR — Accountability | Mandatory human review keeps a named, accountable handler responsible for each response | **Addressed** (by design); org-level SM&CR mapping is **Out of scope** |

---

## 4. DORA

**Applicability.** DORA (Regulation (EU) 2022/2554, applicable from 17 January
2025) binds the financial entity, not the tool. The bot is one ICT asset within
the firm's estate, and Bedrock/AWS is an **ICT third-party service provider**.
References here are at the level of DORA's pillars rather than pinpoint articles.

| Pillar | How the system relates | Status |
|---|---|---|
| ICT risk management | Pseudonymisation, keyed hashing, in-region pinning and audit logging are component-level controls that feed a firm's ICT RMF | **Partial** — controls present, not a managed framework |
| ICT third-party risk | Bedrock/AWS is identified as the ICT third party; residency is pinned; **single-provider concentration risk** is acknowledged | **Partial** — no register of information, exit strategy, or substitutability analysis in the demo |
| ICT incident management & reporting | The audit trail is a foundation for detection and forensics | **Gap** — no incident classification, alerting, or regulatory-reporting workflow |
| Digital operational resilience testing | — | **Gap** — no resilience or threat-led penetration testing |
| Logging | Per-turn append-only audit trail | **Addressed** (app-level) |

---

## 5. EU AI Act

**Classification.** Treating the Act as four independent gates rather than a
pyramid: this system is **not prohibited** (Art 5), is **not high-risk**, and is
**not a GPAI provider obligation** (the firm is a *deployer* building on a
third-party model via API). It is a customer-service-adjacent assistant that
drafts complaint responses for a human handler who reviews and decides every
output — it does not itself determine access to an essential service, and it does
no profiling. Under the Annex III analysis, customer-service AI of this kind is
generally outside the high-risk domains. Where a firm judged a complaints/redress
tool to influence access to financial redress, the Art 6(3) route applies: a
**documented non-high-risk assessment** (narrow procedural task / no significant
influence on the human decision / no profiling), retained under Art 6(4) and
registered under Art 49(2). This document *is* that assessment at the engineering
level; the firm makes the final call.

The operative obligations are therefore **Art 50 transparency** and the
deployer-side duties of **human oversight** and **ensuring provider
documentation**.

*Backdrop:* the Digital Omnibus provisional agreement (7 May 2026) defers the
main high-risk Annex III deadline to 2 December 2027 — relevant only if a firm
reclassifies the system as high-risk; the transparency obligations apply now.

| Obligation | How the system addresses it | Status |
|---|---|---|
| Risk classification | Documented non-high-risk reasoning above (assistive, human-decided, no profiling) | **Addressed** (engineering-level; firm confirms) |
| Art 50 — Transparency | The handler knows outputs are AI-generated; `human_review_required` surfaces low-confidence drafts | **Partial** — disclosure to the *end customer* that a response was AI-assisted is a deployment decision, not yet implemented |
| Human oversight | Mandatory human-in-the-loop: the bot never auto-sends; the handler reviews, edits and owns each response | **Addressed** |
| Provider documentation | Relies on Anthropic/AWS model and service documentation for the underlying model | **Partial** — a deployer should record and retain that documentation |
| Record-keeping | App-level audit trail; full Art 12 logging is a high-risk obligation not triggered here | **Addressed** (proportionate) |

---

## 6. Gaps to close for production

Rolling up the **Partial** and **Gap** statuses above into the work a production
deployment would prioritise:

1. **Vault confidentiality & lifecycle** (GDPR Art 32, DORA resilience) — the
   in-memory, unencrypted re-identification key is the single highest-value
   asset. Production needs encrypted, access-controlled, expiring storage.
2. **Authentication & access control** (GDPR Art 32, SYSC) — replace the
   single-user cookie with per-handler identity and authorisation.
3. **DSAR / erasure workflow** (GDPR Art 15–17) — a process to address and, where
   lawful, erase a subject's data across the Vault and audit trail.
4. **Redaction precision** (GDPR Art 4(5), 25) — entity-typed detection
   (Comprehend / Bedrock Guardrails) to lift 64% precision and catch
   lowercase/untitled names, replacing the regex NER.
5. **Audit durability & fail-closed option** (GDPR Art 30, DORA logging) —
   encrypted, access-controlled, tamper-evident store; a fail-closed mode for
   regulated use.
6. **Third-party governance** (SYSC 8, DORA ICT third-party) — outsourcing
   agreement, DPA, exit strategy, register of information, concentration analysis.
7. **Operational resilience & incident reporting** (PS21/3, DORA) — impact
   tolerances, resilience testing, incident classification and reporting.
8. **Transport security** (GDPR Art 32) — enforce TLS for any non-local
   deployment.
9. **Customer-facing AI disclosure** (AI Act Art 50) — disclose to the
   complainant where a response was AI-assisted.
10. **DPIA** (GDPR Art 35) — for processing complaint PII at scale.

---

## 7. References

- EU AI Act — Article 50 (transparency), Article 6 + Annex III (high-risk
  classification): <https://artificialintelligenceact.eu/>
- Digital Omnibus (7 May 2026) high-risk deadline deferral — secondary commentary.
- DORA — Regulation (EU) 2022/2554, applicable 17 January 2025.
- GDPR — Regulation (EU) 2016/679; UK GDPR / Data Protection Act 2018.
- FCA Handbook — DISP, SYSC; PRA/FCA operational resilience (PS21/3).

*Not legal advice. Article references are indicative and should be confirmed
against the current consolidated text by the deploying firm's compliance
function.*
