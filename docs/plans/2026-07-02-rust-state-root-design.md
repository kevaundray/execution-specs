# Rust-accelerated state root computation — design

**Status:** Implemented
**Date:** 2026-07-02 (design) / 2026-07-03 (implemented)

## Goal

Make state-root computation fast by delegating it to a Rust extension, while
keeping the pure-Python spec (`src/ethereum/merkle_patricia_trie.py`) as the
untouched reference implementation and the default when the extension is absent.

This is **Option 1** from the design discussion: an optional Rust backend behind
the single state-root call site. It is a *new, parallel* fast path — **not** a
revival of the stale `src/ethereum_optimized` package.

### Why not `ethereum_optimized`?

`ethereum_optimized` was written when every fork had its own `state.py`/`trie.py`
(WET copies). Its `monkey_patch()` discovers every fork and overwrites ~25
module-level state functions per fork. State has since been **centralized** into
shared modules (`src/ethereum/state.py`, `src/ethereum/merkle_patricia_trie.py`),
so that per-fork patching strategy no longer matches the codebase. It is also
untested in CI (which is why it silently rotted) and its native dep
(`rust-pyspec-glue`) is architecture-broken on arm64. Reviving it would mean a
full rewrite against the new shared API anyway.

## Architecture

Three pieces:

1. **`eth_trie_rs`** — a new PyO3/maturin crate (in-repo at `rust/eth_trie_rs/`).
   Exposes one function to Python:

   ```
   state_root(accounts, storage) -> bytes32
   ```

   Internally depends on `alloy-trie` + `alloy-primitives`. It builds each
   account's storage-trie root, assembles `TrieAccount(nonce, balance,
   storage_root, code_hash)`, and computes the main state root. It owns keccak,
   RLP, and Patricia hashing — Python does none of it on the fast path.

2. **Dispatch shim** — a module-level `try: import eth_trie_rs` plus a ~3-line
   guard inside `State.compute_state_root_and_trie_changes` in
   `src/ethereum/state.py`. The guard lives there (not in the generic `root()`)
   because the chosen "whole root incl. storage" boundary needs the storage
   tries, which `root()` does not receive. `patricialize()`/`root()` stay 100%
   untouched as the fallback reference.

3. **Packaging** — `eth_trie_rs` is an optional dependency under the existing
   `[optimized]` extra; its absence yields a silent pure-Python fallback.

## FFI data contract

The boundary is "whole root incl. storage": Python marshals the full state
snapshot in one call. All arbitrary-precision numbers cross as **big-endian
bytes** (no lossy int extraction).

```
state_root(
    accounts: list[(addr20: bytes, nonce: bytes, balance: bytes, code_hash: bytes32)],
    storage:  dict[addr20: bytes, list[(key32: bytes, value: bytes)]],
) -> bytes32
```

- **Keys are passed as raw preimages** (20-byte address, 32-byte storage slot).
  Rust does the `keccak256` securing, matching `_prepare_trie`'s "secured tries
  hash keys once before construction." Keeps the hashing convention in one place.
- Storage `value` is the minimal big-endian encoding of the U256; Rust
  RLP-encodes it.
- Empty storage tries (the spec deletes them when `_data == {}`) are simply
  absent from the dict → Rust uses `EMPTY_TRIE_ROOT` for those accounts,
  mirroring `get_storage_root`'s fallback.
- `nonce`/`balance` as minimal big-endian bytes; Rust parses into `U256`/`u64`.

`Account` is `{nonce: Uint, balance: U256, code_hash: Hash32}` — `storage_root`
is *not* stored on the account; it is computed per-account from that account's
storage trie. `alloy`'s `TrieAccount` encodes exactly
`(nonce, balance, storage_root, code_hash)`, byte-identical to the spec's
`encode_account`.

**v1 keeps marshalling simple** (list-of-tuples, per-call rebuild). No persistent
/ incremental trie, no pre-flattened buffers — added only if profiling shows the
marshalling itself is the bottleneck (YAGNI).

## Python dispatch shim

Only `src/ethereum/state.py` changes. Two edits:

**(a) Module-level optional import:**

```python
try:
    import eth_trie_rs as _eth_trie_rs
except ImportError:
    _eth_trie_rs = None
```

**(b) Guard inside `compute_state_root_and_trie_changes`,** slotted in right after
`main_trie` and `storage_tries` are built:

```python
        if _eth_trie_rs is not None:
            accounts = [
                (addr, acct.nonce.to_be_bytes(),
                 acct.balance.to_be_bytes(), acct.code_hash)
                for addr, acct in main_trie._data.items()
                if acct is not None
            ]
            storage = {
                addr: [(k, v.to_be_bytes()) for k, v in t._data.items()]
                for addr, t in storage_tries.items()
            }
            return _eth_trie_rs.state_root(accounts, storage), []

        # --- pure-Python reference (unchanged) ---
        def get_storage_root(addr): ...
        state_root_value = root(main_trie, get_storage_root=get_storage_root)
        return state_root_value, []
```

**Properties:**

- `root()` and `patricialize()` are completely untouched — the reference
  algorithm stays pristine; only the orchestrating method gains a branch.
- Fast path and reference sit in the same method, so a future refactor cannot
  silently desync them (the anti-rot property that `ethereum_optimized` lacked).
- Returning `[]` for the trie-changes list is provably safe: **every** caller
  (all forks' `fork.py`, the `state_root()` wrapper at `state.py:349`, and the
  t8n tool at `t8n_types.py:321`) unpacks the result as `..., _ = ...` and
  discards the second element.

## Correctness & CI (anti-rot safeguard)

We are trusting `alloy-trie` to match the spec byte-for-byte, so parity testing
is load-bearing. `alloy-trie` *is* reth's trie, targeting mainnet conventions,
which the spec also implements — but the differential test is the backstop for
any edge case.

1. **Differential property test (primary).** Generate random states — varied
   accounts and storage tries, including edge cases: empty storage, single-key
   tries, keys sharing long nibble prefixes (exercises extension nodes and the
   sub-32-byte inlining boundary). Assert
   `eth_trie_rs.state_root(...) == <pure-python compute_..._and_trie_changes>`.
   Use a **fixed explicit seed** (the repo bans `random`/`Date.now`-style
   nondeterminism at import time).

2. **Fixture-level differential (integration).** Run the existing `json-loader`
   suite with the backend present and assert produced state roots match the
   expected fixtures. Proves parity on real mainnet-shaped state.

3. **CI job.** New job in `.github/workflows/test.yaml` that installs
   `[optimized]` (builds the crate via maturin), then runs the property test and
   json-loader suite with the backend active. **This is the piece missing for
   `ethereum_optimized` that let it rot.**

4. **Rust-side unit tests** in the crate for marshalling/encoding helpers.

## Packaging, build & repo layout

```
rust/eth_trie_rs/
  Cargo.toml         # pyo3, alloy-trie, alloy-primitives (pinned exactly)
  pyproject.toml     # maturin build backend
  src/lib.rs         # #[pymodule] state_root(...)
```

- **Build backend:** maturin. The crate is its own PyO3 project; the main
  `ethereum-execution` package stays pure-Python and lists `eth_trie_rs` only
  under the `[optimized]` extra.
  - `pip install ethereum-execution` → pure Python, no Rust toolchain needed.
  - `pip install 'ethereum-execution[optimized]'` → builds/pulls the crate; fast
    path activates automatically via the `try: import`.
- **Dependency pinning:** `alloy-trie`/`alloy-primitives` pinned to exact
  versions (consensus-critical; the differential test catches divergence, but
  pinning makes bumps deliberate).

### Scope boundaries (YAGNI)

- **In:** state-root computation only (the "whole root incl. storage" call).
- **Out (v1):** PoW/ethash optimization (`ethereum_optimized/fork.py`, legacy
  pre-Merge only); DB-backed/incremental persistent state (`state_db.py`'s
  bigger role); any change to `patricialize`/`root` themselves.
- **`ethereum_optimized`:** left untouched — this is a separate parallel fast
  path. Whether to formally deprecate it is a later decision.

## Open items for implementation — resolved

- **alloy-trie API confirmed** (via `alloy-trie` 0.7.4): secured key/value pairs
  are fed to `HashBuilder::add_leaf(Nibbles::unpack(keccak256(key)), &value)` in
  sorted-by-hashed-key order; the account leaf is the derived `RlpEncodable`
  `[nonce, balance, storage_root, code_hash]`. `alloy-primitives`/`alloy-rlp`
  pinned exactly alongside `alloy-trie`.
- **Storage `U256` RLP confirmed** to match the spec: the differential + 50-seed
  property tests in `tests/json_loader/test_rust_state_root.py` pass, proving
  byte-identical roots including populated storage tries.
- **`[optimized]` extra:** kept the existing `rust-pyspec-glue`/`ethash` entries
  (they cover the separate PoW/legacy path, out of scope here) and documented
  `eth_trie_rs` as a comment there (built from source via
  `uv pip install -e rust/eth_trie_rs/`; PyPI dependency line noted for when it
  is published).
