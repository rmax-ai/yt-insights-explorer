# V2 contract fixtures

These fixtures are vendored from the authoritative `yt-insights` V2 contract
at producer commit `6b97d71`. The goldens are copied byte-for-byte from
`tests/fixtures/v2/`; the negative fixtures cover duplicate persisted IDs,
dangling evidence references, and a schema-version mismatch.

The explorer tests use these files without requiring the private producer
checkout.
