# eth_trie_rs

An **optional** Rust extension that accelerates Ethereum state-root computation
for the [execution-specs](../../). It is a drop-in fast path behind
`ethereum.state.compute_state_root_and_trie_changes`: when the compiled module
is importable, that method delegates root computation to this crate; when it is
absent, the pure-Python spec runs unchanged.

**The pure-Python spec remains the canonical reference.** This crate is a
performance optimization only and is *not* part of the specification. The
pure-Python `root(...)` path in `src/ethereum/state.py` /
`src/ethereum/merkle_patricia_trie.py` defines correct behavior; this crate must
produce a byte-identical root.

## What it exposes

A single function:

```
state_root(accounts, storage) -> bytes32
```

- `accounts` — a `list` of `(addr20, nonce_be, balance_be, code_hash32)` tuples:
  - `addr20`   — the 20-byte account address (**raw preimage**, not hashed)
  - `nonce_be` — the nonce as minimal big-endian bytes (parsed into a `u64`)
  - `balance_be` — the balance as minimal big-endian bytes (parsed into a `U256`)
  - `code_hash32` — the 32-byte code hash
- `storage` — a `dict` mapping `addr20 -> [(slot32, value_be)]`:
  - `slot32`   — the 32-byte storage slot key (**raw preimage**, not hashed)
  - `value_be` — the slot value as minimal big-endian bytes (parsed into a
    `U256`, then RLP-encoded)

Returns the 32-byte state root as `bytes`.

Accounts whose address is absent from `storage` (or whose slot list is empty)
use `EMPTY_ROOT_HASH` for their storage root, mirroring the spec, which deletes
empty storage tries.

## Raw preimages in, Rust secures the keys

Callers pass **raw preimages** for both account addresses and storage slots. The
crate applies `keccak256` to each key internally before inserting it into the
trie — it builds a *secured* trie. This mirrors the spec's secured trie
(`_prepare_trie` hashes keys once before construction) and keeps the key-hashing
convention in exactly one place rather than splitting it across the FFI boundary.
Integers (nonce, balance, storage value) cross the boundary as minimal
big-endian bytes so no arbitrary-precision value is lost to a lossy int
extraction.

The account leaf value is `alloy_trie::TrieAccount`-style RLP of
`[nonce, balance, storage_root, code_hash]`, byte-identical to the spec's
`encode_account`.

## Build / install

```
uv pip install -e rust/eth_trie_rs/
```

> Do **not** use `uv run maturin develop` — in this repo that resolves a stale
> maturin 0.14, which will not build the crate. `uv pip install -e` uses the
> maturin build backend declared in this crate's `pyproject.toml` correctly.

The crate is listed (as a documented comment) under the `[optimized]` extra in
the top-level `pyproject.toml`; installing it makes the fast path activate
automatically via the `try: import eth_trie_rs` guard in
`src/ethereum/state.py`. If the module is not importable, that guard sets the
backend to `None` and the pure-Python reference runs.

## Dependency pins (consensus-critical)

`alloy-trie`, `alloy-primitives`, and `alloy-rlp` are pinned to **exact**
versions in `Cargo.toml`:

```
alloy-trie      = "=0.7.4"
alloy-primitives = "=0.8.5"
alloy-rlp        = "=0.3.8"
```

These libraries own the trie hashing and RLP encoding that produce the state
root. An encoding change in any of them would **silently fork the state root**
without changing the FFI signature. Exact pins make every bump deliberate: a
version bump must be reviewed and **re-validated against the differential
tests** below before it is accepted.

## Parity is enforced

`tests/json_loader/test_rust_state_root.py` is the source of truth for parity. It
runs differential tests (empty state, single account, accounts with storage) plus
a 50-seed property test that builds random states and asserts the Rust root
equals the pure-Python root — and one test that forces the fallback and asserts
it agrees with the backend. The CI job `eth-trie-rs` in
`.github/workflows/test.yaml` builds this crate and runs that suite, so any
divergence (including from a dependency bump) fails CI.

## v1 length contract (trusted callers)

The FFI boundary trusts its caller — the Python shim in
`src/ethereum/state.py`, which always marshals well-formed inputs. The crate
assumes: address = 20 bytes, code hash and storage keys = 32 bytes,
balance/storage values ≤ 32 bytes big-endian, and nonce fits in a `u64`.
Malformed lengths **panic** (via `B256::from_slice` / `U256::from_be_slice`)
rather than raising a Python `PyErr`. A nonce of 2**64 or greater is likewise
out of contract (barred by EIP-2681's nonce cap) and will **panic** rather than
silently truncate. This is acceptable for the current
internal-only v1 boundary. Hardening these into proper `PyErr` returns is a
deferred follow-up, to be done before any untrusted caller is exposed.
