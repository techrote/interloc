# Protocol schemas

These Draft 2020-12 JSON Schema documents are the portable shape contract for protocol v1. Runtime validation in `src/interloc/protocol/` is authoritative for cross-field, time, byte-size, digest and path-safety rules that JSON Schema cannot completely express.

All top-level envelopes are closed (`additionalProperties: false`). Capability argument objects are discriminated by `capability` and closed independently. Unknown schema versions and capabilities fail closed; future versions require explicit code/schema support.
