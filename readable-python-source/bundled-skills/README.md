# Bundled skills

`omni_body_skill` has a single authoritative source at
`backend/omni_body_skill/`.

The previous byte-for-byte bundled copy was removed because two writable
authorities for the same skill allow security fixes to drift. Packaging must
copy from the canonical directory at build time and verify the release
manifest instead of committing a second source tree.
