# Drugref reviewer GUI finalization design

**Date:** 2026-08-20
**Status:** Accepted and implemented
**Scope:** issue #86 compatibility, counter-sign completion and removal of the unfinished
global evidence-library affordance

## Problem

`drugref.curated_signature_status` published `signed_by_revoked_key` for both a
registered revoked key and a fingerprint the registry had never known. The verifier
already distinguishes those cases and ranks `unknown_key` as the stronger objection.
The reviewer service passed the coarse database string through an open `String`, and
the GUI rendered the machine spelling without a human label.

The sidebar also exposed a disabled Evidence library item. Evidence capture itself is
complete and intentionally target-scoped: immutable citations and their Markdown
context live beside the clinical question they inform. No accepted design specifies a
second global evidence workflow.

## Decision

Migration `db/048` widens the published registry-level vocabulary to:

- `unsigned`: no signature row exists;
- `signed`: at least one signature is registry-unobjected;
- `signed_by_revoked_key`: signatures exist, every one is objected, and all keys are known;
- `signed_by_unknown_key`: signatures exist, every one is objected, and at least one key is unknown.

The precedence is `signed` first, then `signed_by_unknown_key`, then
`signed_by_revoked_key`. One independently unobjected signature therefore continues to
resolve a counter-sign task. When every signature is objected, the more suspicious
unknown-key fact is visible.

The shared Rust domain and TypeScript WebView contracts use closed four-value types.
Unexpected database values fail as an internal service error instead of silently
entering the GUI. Revision history and signing controls render explicit human labels;
objected states use the existing danger palette. The signing control admits every
state except `signed`, so a revision returned as a counter-sign task can actually be
attested from the GUI.

The disabled global Evidence library navigation item is removed. Reviewers continue to
attach and inspect citations in each target's immutable working record. Building a
cross-target research catalog would require its own query, paging, identity and reuse
semantics and is not implied by the existing record model.

Browser-preview trust administration also projects a simulated blanket compromise into
the current preview decision. This keeps the layout-only adapter capable of exercising
the same counter-sign state returned by the production service without becoming a
native fallback or a second authority.

## Preserved boundaries

- PostgreSQL reports registry policy only and never claims to verify Ed25519.
- `drugref verify` retains its six detailed verdicts and remains the cryptographic check.
- Clinical rows remain served in every signature state.
- Signatures, status corrections, annotations and evidence references remain immutable.
- Bearer tokens, private keys and canonical payload bytes remain outside the WebView.
- Release-manifest signing, class-grain signing and public installer distribution are out of scope.

## Verification

Regression tests pin the unknown-only and mixed unknown/revoked cases. Existing tests
continue to pin that one unobjected signature wins and that revocation never removes a
clinical row. Domain tests pin the exact wire vocabulary; service, native, Svelte,
frontend-build and native-launch checks cover the remaining boundaries.
