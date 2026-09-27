# Chapter Transaction v2

Checkout supplies a v2 lease bound to the current chapter, state hash, rolling blueprint hash and expiry. Submit `lease_id`, `chapter_number`, `title`, `content`, and an `actual` object. The delta arrays are optional: `events`, `state_changes`, `relationship_changes`, `foreshadow_ops`, `emotional_transactions`.

Events have unique string IDs and a status of `progressed`, `turned`, or `closed`. Participants, outcome tags and evidence terms are string arrays; time fields are integers. State changes name a recorded character and a supported field. An optional `from` value is a compare-and-set precondition. These are data contracts, not prose interpretation.

Submission records bytes, delta provenance, planned event IDs missing from the report, and literal evidence-term presence. A literal match proves only that text occurs. It does not prove an event, emotional payoff or user-goal compliance. No minimum character count or deviation score rejects a chapter.

`CHAPTER_COMMITTED` / `committed=true` establishes storage completion only. Reopen the returned path and bind its hash to the final adversarial judge's decision. A failed storage step can leave a prepared transaction: recover it before another submit. Recovery validates state/content/ledger identity before writing and refuses conflicting live bytes or versions. It never implies content approval.

Old v1 leases are rejected explicitly. Old v1 prepared transactions remain recoverable as byte transactions; their original schema and history are preserved.

To repair the latest recorded chapter, read its chapter record and actual bytes, then checkout that chapter with `revision_of` equal to its current SHA-256. Submit the fresh lease with revised prose and `actual={}`. This changes prose only; prior story annotations remain explicitly unverified. Earlier chapter or state-delta corrections are outside this version’s revision support and must not be silently rewritten. Old bytes/records remain in transaction history. Read the new content and obtain a fresh final judge decision.
