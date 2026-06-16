# Anonymization — the trust architecture (for the buyer and the auditor)

> **The one-line claim.** OntoForge never sees your raw data. You run a local
> toolkit that anonymizes records on your own machine, you hold the only key that
> can turn a token back into a real value, and the cloud engine computes only on
> the anonymized input. The answers come back wearing tokens; the toolkit
> deciphers them locally, against your key, after the result has crossed back.
>
> The trust question stops being *"do I trust their SOC 2?"* and becomes *"they
> **cannot** see my identifiable data."* That is a mathematical answer to a
> procurement question, not a promise — and it is exactly the posture a
> bundle-everything incumbent structurally cannot match.

This document explains the boundary two ways: once for a **buyer** deciding
whether the architecture collapses their compliance burden, and once for an
**auditor** who wants to verify the claim rather than take it on faith. It also
states plainly what is and is not built today, because an honest boundary is the
only kind worth auditing.

---

## 1. Why this exists (the structural argument)

A solo company cannot out-*attest* a hyperscaler. There is no SOC 2 Type II that
beats Microsoft's SOC 2 Type II. So OntoForge does not try to win the attestation
race — it removes the question the attestation was meant to answer.

If the customer **holds the traceable-ID key** and the cloud computes only on
**anonymized input**, then the sensitive question — *can the vendor see my
identifiable data?* — has a structural answer: no, because the identifiable
values never left the customer's environment in the first place. A breach of the
cloud exposes anonymized data, not PII. A subpoena served on the vendor returns
tokens, not identities. The blast radius of any incident is capped by
construction.

This is the one kind of trust a solo founder can credibly build, because it is an
**architecture decision, not a certification or a headcount**. It scales to zero
employees.

---

## 2. The boundary (what crosses, what doesn't)

```
┌─────────────────────────────────────────┐
│  YOUR ENVIRONMENT (your machine / VPC)   │
│                                          │
│   raw records ──▶ anonymize(key) ──▶ tokens
│                       ▲                  │
│                  the KEY lives here      │
│                  and never leaves        │
└───────────────────────┬──────────────────┘
                        │  anonymized input only
                        ▼
┌─────────────────────────────────────────┐
│  ONTOFORGE COMPUTE (cloud or your VPC)   │
│                                          │
│   induce · resolve · validate · answer   │
│   — operates on tokens, returns tokens   │
│   — sees no identifiable value, ever     │
└───────────────────────┬──────────────────┘
                        │  token-bearing results
                        ▼
┌─────────────────────────────────────────┐
│  YOUR ENVIRONMENT                        │
│                                          │
│   token-bearing answer ──▶ decipher(key) │
│                       ──▶ real answer     │
└─────────────────────────────────────────┘
```

The only thing that ever crosses the boundary toward OntoForge is **anonymized
input**. The only thing that crosses back is a **token-bearing result**. The key
that maps tokens to identities is generated, stored, and used **exclusively inside
your environment**. OntoForge has no copy and no way to request one.

---

## 3. Why the engine still works on anonymized data (the join-preserving property)

The naïve objection is: *"if you scramble my data, how can your engine still find
the join between two tables?"* The answer is that the anonymization is
**structure-preserving**, not destructive.

OntoForge's engine does not need to read a customer's literal values to do its
job. It works on **relationships between values**:

- **Equality is preserved.** The same raw value always maps to the same token. So
  if `tail_number = "N172SP"` appears in a registry table and in a maintenance
  table, both become the *same* token — and the engine's join discovery
  (`relationships`) sees the value overlap exactly as it would on raw data. The
  false-positive killer (distribution divergence) and the execute-the-join
  validation (`validation`) both operate on this preserved equality structure.
- **Distribution shape is preserved.** Cardinality, null rate, value-set overlap,
  and key-uniqueness are computed over tokens and are identical to the same
  statistics over the raw values, because tokenization is a bijection on the
  value domain. The confidence proxy is unchanged.
- **Type and format signatures are preserved where they must be.** Numeric
  measures stay numeric and order-preserving where a measure needs to be summed
  or ranged; categorical identifiers become opaque tokens; free-text spans are
  handled by the toolkit's field-class policy. The customer declares, per column,
  what semantic shape must survive — and the toolkit guarantees only that shape
  crosses, nothing more.

The result: the induced ontology, the resolved entities, the typed relationships,
and the per-value provenance ledger are all **computed correctly on tokens**, and
every citation the engine returns points at a token. When the result comes home,
the toolkit deciphers each token back to its real value against the local key. The
customer reads a normal, fully-identified answer; OntoForge only ever touched
tokens.

> **Provenance survives the round trip.** AMBER snapshots and the provenance
> ledger reference tokens, so an exported estate is *also* anonymized at rest. A
> customer who holds the key can replay and decipher it; a third party who obtains
> the bundle without the key holds only tokens. The $0-exit guarantee and the
> anonymization boundary compose cleanly.

---

## 4. The one-click flow (what the customer actually does)

1. **Anonymize.** Point the local toolkit at your source directory (or connector).
   It generates a key on first run, stores it in your environment, and emits an
   anonymized copy whose structure the engine can consume. The key never touches
   the wire.
2. **Compute.** OntoForge runs the pipeline — induce, resolve, materialize, ask —
   over the anonymized input. Everything it returns is token-bearing.
3. **Decipher.** The toolkit takes the token-bearing answer (or the exported
   bundle) and reverses the tokens against your local key, locally. You see the
   real values; OntoForge never did.

To the user it is two clicks bracketing the normal workflow: **anonymize on the
way in, decipher on the way out.** The key is the hinge, and it stays home.

---

## 5. Honesty note — demo-grade cipher vs. production KMS

We will not oversell the cryptography, because overselling it is the fastest way
to lose the audit.

- **The current/reference toolkit uses a demo-grade, deterministic tokenization
  cipher.** It is correct about the *property* that matters — same value → same
  token, structure preserved, key held locally — and it is sufficient to
  demonstrate and audit the boundary end to end. It is **not** a hardened
  cryptographic deployment.
- **A production deployment wants a real key-management system.** That means
  keys generated and rotated in a customer-controlled KMS or HSM (e.g. cloud KMS,
  on-prem HSM), per-field/per-tenant key derivation, authenticated tokenization,
  and an explicit threat model for the residual inference risks that *any*
  deterministic tokenization scheme carries (frequency analysis on low-cardinality
  fields; correlation across joins). The toolkit's field-class policy is where a
  customer trades off join utility against those residual risks.
- **What does not change between demo-grade and production** is the architecture:
  the key lives in the customer's environment, OntoForge computes on tokens, and
  the result is deciphered locally. The cipher is an implementation detail behind a
  boundary that is the actual product.

Stating this distinction in writing is deliberate. A trust product that hides its
own limitations is not a trust product.

---

## 6. The open-source stance (auditing is the point)

The anonymization toolkit lives on the **open shell**, not the closed core (see
[IP_ARCHITECTURE.md](IP_ARCHITECTURE.md): the `anonymizer` package is listed as
open-shell, customer-facing tooling). This is a deliberate trust play, not an
oversight.

- **Open-sourcing the toolkit lets the customer audit the boundary themselves.**
  The whole claim is *"we never see your raw data."* The only way to make that
  claim verifiable rather than asserted is to let the customer (and their auditor,
  and their security team) read the exact code that runs in their environment, on
  their machine, against their key.
- **The moat does not live in the toolkit.** OntoForge's proprietary inventions —
  the distribution-aware confidence proxy, the execute-the-join validator, the
  typed-relationship ensemble, the bitemporal provenance store, the ontology
  induction and evolution calculus — are the **closed core**. The anonymizer is a
  client-side, commodity-shaped boundary tool. Opening it costs no moat and buys
  maximal trust. That trade is the right one.
- **Auditability extends to the wire.** Because the toolkit is the only thing that
  produces the bytes sent to OntoForge, an auditor can capture the egress and
  confirm it contains tokens, not values — and can do so without trusting any
  OntoForge attestation at all.

---

## 7. The compliance wedge (for the regulated buyer)

For an EU-exposed or regulated buyer (including FAA/DoD-adjacent procurement), the
customer-holds-the-key boundary is the cleanest possible answer to several
overlapping obligations at once:

- **Data minimization / GDPR posture.** The vendor never holds identifiable data,
  so a large part of the data-processing-agreement and DPIA burden collapses: there
  is no PII processing to assess on the vendor side.
- **EU AI Act Article 10 (data governance).** The boundary composes with
  OntoForge's per-value provenance and as-of reconstruction — the ledger records
  *which token* produced *which answer*, and the customer can reconstruct the
  identified lineage locally.
- **Sovereignty / DORA-style residency.** Identifiable values never leave the
  customer's jurisdiction or environment; only tokens transit.
- **Breach blast radius.** A vendor-side incident exposes anonymized data, not
  identities. This is a structural cap, not a procedural mitigation.

The buyer's security review changes shape: instead of auditing whether the vendor
*handles* PII correctly, the buyer confirms — by reading open-source code and
capturing the egress — that the vendor never *receives* PII at all. For a first
design partner without a SOC 2 on file, this is frequently a shorter path to "yes"
than a certification would be, because it removes the data-exposure question
rather than auditing it.

---

## 8. For the auditor — what to verify, and how

A skeptical auditor should be able to confirm every claim above without trusting
OntoForge's word. The checklist:

1. **The key never leaves.** Read the open-source toolkit; confirm key generation
   and storage are local and that no code path transmits the key. Capture network
   egress during an `anonymize` run and confirm the key is absent.
2. **Only tokens cross.** Capture the egress during a full pipeline run and
   confirm the payload contains tokens, not raw values. Spot-check that a known
   sensitive value never appears on the wire.
3. **Equality is the only thing preserved by default.** Confirm that the
   tokenization is a deterministic bijection per declared field-class, and that the
   field-class policy is the explicit, customer-controlled knob trading join
   utility against residual inference risk.
4. **The result is deciphered locally.** Confirm the decipher step runs in the
   customer environment against the local key, and that OntoForge's returned
   payload is token-bearing.
5. **Provenance and export stay anonymized.** Confirm AMBER bundles and the
   provenance ledger reference tokens, so the exported estate is anonymized at rest
   and requires the local key to decipher.
6. **The honesty note holds.** Confirm the cipher in use is the documented one
   (demo-grade today; KMS-backed in production), and that the residual-risk threat
   model is stated rather than hidden.

If all six hold, the headline claim — *we never see your raw data* — is verified,
not asserted.

---

## 9. Status and sequencing (honest)

- **Architected now, built after connectors + auth.** The open-shell `anonymizer`
  slot exists in the IP boundary today (IP_ARCHITECTURE.md). The toolkit is
  correctly sequenced **after** connectors, authentication, and multi-tenant
  observability, because anonymization has no value until real customer data flows
  through a multi-tenant product. Building the headline trust wedge before there is
  a product to flow data through would be the wrong order.
- **Messaged now.** The boundary is a story that opens doors before the final code
  ships — the way a roadmap commitment does in regulated sales — *provided* the
  status is stated honestly, which is the purpose of this section and of §5.
- **The runtime engine remains keyless and offline.** Nothing in the anonymization
  boundary changes the core invariant: the engine ships keyless, runs
  deterministically, and requires no network at runtime. Anonymization is a
  boundary the customer operates around that engine, not a dependency inside it.

---

*See also: [IP_ARCHITECTURE.md](IP_ARCHITECTURE.md) (closed-core vs. open-shell
boundary; the `anonymizer` open-shell slot), and the site trust pages
(`site/index.html#trust`, `site/trust.html`) for the buyer-facing version of this
architecture.*
