# Rust-accelerated State Root Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional Rust extension (`eth_trie_rs`) that computes the
Ethereum state root, wired behind the single call site in
`src/ethereum/state.py`, with the pure-Python spec kept as the untouched
reference and default.

**Architecture:** A new PyO3/maturin crate at `rust/eth_trie_rs/` exposes one
function `state_root(accounts, storage) -> bytes32`, implemented with
`alloy-trie`. `State.compute_state_root_and_trie_changes` in
`src/ethereum/state.py` gains a `try: import`-gated fast-path branch that
marshals the state snapshot to Rust; when the extension is absent it falls
through to the existing `root()`/`patricialize()` code, which is not modified.
A seeded differential property test plus the existing `json-loader` fixtures are
the parity oracle, enforced by a new CI job.

**Tech Stack:** Rust, PyO3, maturin, `alloy-trie` + `alloy-primitives` +
`alloy-rlp`, Python 3.13, pytest, `uv`, `just`.

**Full design:** `docs/plans/2026-07-02-rust-state-root-design.md`

**Background:** The existing `src/ethereum_optimized` package is stale — its
per-fork monkey-patch strategy predates the shared-`State` refactor
(`tests/json_loader/test_optimized_state.py` is skipped wholesale, see issue
#2256). This work is a **separate, parallel** fast path, not a revival.

---

## Reference: exact code locations

- State root call site (the tail to branch on):
  `src/ethereum/state.py:249-256` (`get_storage_root` closure + the two `root()`
  calls, inside `compute_state_root_and_trie_changes`).
- Imports block to extend: `src/ethereum/state.py:16-41`.
- `Account` dataclass: `src/ethereum/state.py:52-59`
  (`nonce: Uint`, `balance: U256`, `code_hash: Hash32`).
- `encode_account` (RLP `[nonce, balance, storage_root, code_hash]`):
  `src/ethereum/merkle_patricia_trie.py:199-217`.
- `EMPTY_TRIE_ROOT`: exported from `ethereum.merkle_patricia_trie`
  (= `keccak256(rlp.encode(b""))`).
- `[optimized]` extra: `pyproject.toml:198-201`.
- CI test workflow: `.github/workflows/test.yaml`.

**Run a single test:** `uv run pytest <path>::<name> -v`
**Build the crate into the venv:** `uv run maturin develop -m rust/eth_trie_rs/Cargo.toml`

---

## Task 1: Crate scaffold + trivial pymodule (empty state root)

**Files:**
- Create: `rust/eth_trie_rs/Cargo.toml`
- Create: `rust/eth_trie_rs/pyproject.toml`
- Create: `rust/eth_trie_rs/src/lib.rs`
- Test: `tests/json_loader/test_rust_state_root.py`

**Step 1: Write the failing test**

```python
# tests/json_loader/test_rust_state_root.py
"""Differential tests: eth_trie_rs state root vs the pure-Python spec."""

import pytest

from ethereum.merkle_patricia_trie import EMPTY_TRIE_ROOT

eth_trie_rs = pytest.importorskip("eth_trie_rs")


def test_empty_state_root() -> None:
    assert eth_trie_rs.state_root([], {}) == bytes(EMPTY_TRIE_ROOT)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/json_loader/test_rust_state_root.py -v`
Expected: SKIPPED ("could not import 'eth_trie_rs'") — the crate does not exist
yet. (importorskip makes "not built yet" a skip, not a hard fail; that is
intended. The test starts passing once the crate builds.)

**Step 3: Write the crate**

`rust/eth_trie_rs/Cargo.toml`:
```toml
[package]
name = "eth_trie_rs"
version = "0.1.0"
edition = "2021"

[lib]
name = "eth_trie_rs"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
alloy-trie = "=0.7.0"          # pin exactly; consensus-critical
alloy-primitives = "=0.8.0"    # pin exactly
alloy-rlp = { version = "=0.3.0", features = ["derive"] }
```

`rust/eth_trie_rs/pyproject.toml`:
```toml
[build-system]
requires = ["maturin>=1.5,<2"]
build-backend = "maturin"

[project]
name = "eth_trie_rs"
version = "0.1.0"
requires-python = ">=3.11"

[tool.maturin]
module-name = "eth_trie_rs"
```

`rust/eth_trie_rs/src/lib.rs`:
```rust
use alloy_primitives::{keccak256, B256, U256};
use alloy_trie::{HashBuilder, Nibbles};
use alloy_rlp::RlpEncodable;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;

/// RLP `[nonce, balance, storage_root, code_hash]` — matches the spec's
/// `encode_account`.
#[derive(RlpEncodable)]
struct TrieAccount {
    nonce: u64,
    balance: U256,
    storage_root: B256,
    code_hash: B256,
}

/// Compute a secured-trie root from raw (preimage, rlp_value) pairs.
/// Keys are keccak256-hashed here (secured trie) and added in sorted order.
fn secured_root(mut leaves: Vec<(B256, Vec<u8>)>) -> B256 {
    // HashBuilder requires leaves added in ascending nibble order.
    leaves.sort_by(|a, b| a.0.cmp(&b.0));
    let mut hb = HashBuilder::default();
    for (hashed_key, value) in leaves {
        hb.add_leaf(Nibbles::unpack(hashed_key), &value);
    }
    hb.root()
}

#[pyfunction]
fn state_root(
    py: Python<'_>,
    accounts: Vec<(Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>)>,
    storage: HashMap<Vec<u8>, Vec<(Vec<u8>, Vec<u8>)>>,
) -> PyResult<Py<PyBytes>> {
    let mut account_leaves: Vec<(B256, Vec<u8>)> = Vec::with_capacity(accounts.len());

    for (addr, nonce, balance, code_hash) in accounts {
        let storage_root = match storage.get(&addr) {
            Some(slots) if !slots.is_empty() => {
                let leaves = slots
                    .iter()
                    .map(|(k, v)| {
                        // storage value is minimal big-endian U256; RLP-encode it
                        let val = U256::from_be_slice(v);
                        (keccak256(k), alloy_rlp::encode(val))
                    })
                    .collect();
                secured_root(leaves)
            }
            _ => alloy_trie::EMPTY_ROOT_HASH,
        };

        let account = TrieAccount {
            nonce: u64_from_be(&nonce),
            balance: U256::from_be_slice(&balance),
            storage_root,
            code_hash: B256::from_slice(&code_hash),
        };
        account_leaves.push((keccak256(&addr), alloy_rlp::encode(account)));
    }

    let root = secured_root(account_leaves);
    Ok(PyBytes::new_bound(py, root.as_slice()).unbind())
}

fn u64_from_be(bytes: &[u8]) -> u64 {
    let mut buf = [0u8; 8];
    let start = 8usize.saturating_sub(bytes.len());
    buf[start..].copy_from_slice(&bytes[bytes.len().saturating_sub(8)..]);
    u64::from_be_bytes(buf)
}

#[pymodule]
fn eth_trie_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(state_root, m)?)?;
    Ok(())
}
```

> **Note for implementer:** verify `alloy-trie` `HashBuilder::add_leaf` /
> `Nibbles::unpack` / `EMPTY_ROOT_HASH` signatures against the pinned `0.7.0`
> docs; the crate's API has shifted across versions. Adjust the exact calls but
> keep the semantics (secured keys, sorted insertion, `TrieAccount` RLP order).

**Step 4: Build and run test to verify it passes**

Run:
```
uv run maturin develop -m rust/eth_trie_rs/Cargo.toml
uv run pytest tests/json_loader/test_rust_state_root.py::test_empty_state_root -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add rust/eth_trie_rs tests/json_loader/test_rust_state_root.py
git commit -m "feat(rust): eth_trie_rs crate scaffold with empty state root"
```

---

## Task 2: Single account, no storage (differential)

**Files:**
- Modify: `tests/json_loader/test_rust_state_root.py`

**Step 1: Add a helper that runs the pure-Python oracle, plus the test**

```python
from ethereum.state import Account, State, set_account
from ethereum_types.bytes import Bytes20, Bytes32
from ethereum_types.numeric import U256, Uint
from ethereum.crypto.hash import keccak256

EMPTY_CODE_HASH = keccak256(b"")


def python_state_root(accounts, storage):
    """Reference root via the spec's State, for a dict-based snapshot."""
    state = State()
    for addr, acct in accounts.items():
        set_account(state, addr, acct)
    for addr, slots in storage.items():
        for slot, value in slots.items():
            from ethereum.state import set_storage
            set_storage(state, addr, slot, value)
    from ethereum.state import state_root
    return bytes(state_root(state))


def rust_state_root(accounts, storage):
    acc_list = [
        (bytes(a), acct.nonce.to_be_bytes(), acct.balance.to_be_bytes(),
         bytes(acct.code_hash))
        for a, acct in accounts.items()
    ]
    stg = {
        bytes(a): [(bytes(k), v.to_be_bytes()) for k, v in slots.items()]
        for a, slots in storage.items()
    }
    return eth_trie_rs.state_root(acc_list, stg)


def test_single_account_no_storage() -> None:
    addr = Bytes20(b"\x11" * 20)
    accounts = {addr: Account(Uint(7), U256(1000), EMPTY_CODE_HASH)}
    assert rust_state_root(accounts, {}) == python_state_root(accounts, {})
```

> **Note:** confirm the exact `set_account`/`set_storage`/`state_root` public
> API names in `src/ethereum/state.py` before writing (they exist as
> module-level functions). Adjust imports to match.

**Step 2: Run to verify** — Run:
`uv run pytest tests/json_loader/test_rust_state_root.py::test_single_account_no_storage -v`
Expected: PASS (Task 1's `lib.rs` already handles this path). If FAIL, the
`TrieAccount` field order / balance encoding is the culprit — fix in `lib.rs`.

**Step 3: Commit**

```bash
git add tests/json_loader/test_rust_state_root.py
git commit -m "test(rust): differential single-account state root"
```

---

## Task 3: Accounts with storage (differential)

**Files:**
- Modify: `tests/json_loader/test_rust_state_root.py`

**Step 1: Write the test**

```python
def test_accounts_with_storage() -> None:
    a1 = Bytes20(b"\x11" * 20)
    a2 = Bytes20(b"\x22" * 20)
    accounts = {
        a1: Account(Uint(1), U256(5), EMPTY_CODE_HASH),
        a2: Account(Uint(0), U256(0), EMPTY_CODE_HASH),
    }
    storage = {
        a1: {Bytes32(b"\x00" * 31 + b"\x01"): U256(42),
             Bytes32(b"\xff" * 32): U256(2**200)},
    }
    assert rust_state_root(accounts, storage) == python_state_root(accounts, storage)
```

**Step 2: Run to verify** — Run:
`uv run pytest tests/json_loader/test_rust_state_root.py::test_accounts_with_storage -v`
Expected: PASS. If FAIL, inspect storage-value RLP encoding (`encode_node` of a
`U256` in the spec vs `alloy_rlp::encode(U256)`) and the empty-storage fallback.

**Step 3: Commit**

```bash
git add tests/json_loader/test_rust_state_root.py
git commit -m "test(rust): differential state root with storage tries"
```

---

## Task 4: Seeded property-based differential test (the parity oracle)

**Files:**
- Modify: `tests/json_loader/test_rust_state_root.py`

**Step 1: Write the test.** Deterministic PRNG with a **fixed seed** (the repo
forbids implicit nondeterminism). Cover edge cases: empty storage, single-key
tries, keys with shared long prefixes (extension nodes), values near the
32-byte inlining boundary.

```python
import random


def _rand_state(rng):
    accounts, storage = {}, {}
    n = rng.randint(0, 20)
    for _ in range(n):
        addr = Bytes20(bytes(rng.randrange(256) for _ in range(20)))
        accounts[addr] = Account(
            Uint(rng.randrange(2**64)),
            U256(rng.randrange(2**256)),
            EMPTY_CODE_HASH,
        )
        if rng.random() < 0.5:
            slots = {}
            for _ in range(rng.randint(1, 8)):
                key = Bytes32(bytes(rng.randrange(256) for _ in range(32)))
                slots[key] = U256(rng.randrange(1, 2**256))
            storage[addr] = slots
    return accounts, storage


@pytest.mark.parametrize("seed", range(50))
def test_property_differential(seed: int) -> None:
    rng = random.Random(seed)  # explicit seed → reproducible
    accounts, storage = _rand_state(rng)
    assert rust_state_root(accounts, storage) == python_state_root(accounts, storage)
```

**Step 2: Run to verify** — Run:
`uv run pytest tests/json_loader/test_rust_state_root.py -k property -v`
Expected: 50 PASS. Any failing seed is a real parity bug — fix `lib.rs`, do not
weaken the test.

**Step 3: Commit**

```bash
git add tests/json_loader/test_rust_state_root.py
git commit -m "test(rust): seeded property-based state-root parity oracle"
```

---

## Task 5: Wire the Python dispatch shim into state.py

**Files:**
- Modify: `src/ethereum/state.py` (imports block `16-41`; method tail `~249-256`)
- Test: `tests/json_loader/test_rust_state_root.py`

**Step 1: Write the failing test** — asserts the shim path (through
`compute_state_root_and_trie_changes`) equals the forced pure-Python path.

```python
def test_shim_matches_pure_python(monkeypatch) -> None:
    import ethereum.state as st
    a = Bytes20(b"\x33" * 20)
    accounts = {a: Account(Uint(3), U256(9), EMPTY_CODE_HASH)}
    storage = {a: {Bytes32(b"\x00" * 31 + b"\x07"): U256(123)}}

    fast = python_state_root(accounts, storage)  # backend active (default)

    monkeypatch.setattr(st, "_eth_trie_rs", None)  # force fallback
    slow = python_state_root(accounts, storage)

    assert fast == slow
```

**Step 2: Run to verify it fails** — Run:
`uv run pytest tests/json_loader/test_rust_state_root.py::test_shim_matches_pure_python -v`
Expected: FAIL with `AttributeError: ... has no attribute '_eth_trie_rs'`
(the symbol does not exist yet).

**Step 3: Add the shim.** In `src/ethereum/state.py`, after the imports block
(around line 41) add:

```python
try:
    import eth_trie_rs as _eth_trie_rs
except ImportError:
    _eth_trie_rs = None
```

Then in `compute_state_root_and_trie_changes`, immediately before the
`def get_storage_root` closure (line ~249), insert:

```python
        if _eth_trie_rs is not None:
            accounts = [
                (addr, account.nonce.to_be_bytes(),
                 account.balance.to_be_bytes(), account.code_hash)
                for addr, account in main_trie._data.items()
                if account is not None
            ]
            storage = {
                addr: [(k, v.to_be_bytes()) for k, v in t._data.items()]
                for addr, t in storage_tries.items()
            }
            return Root(_eth_trie_rs.state_root(accounts, storage)), []
```

Leave the existing `get_storage_root` closure and `root(...)` call untouched as
the fallback below the branch.

**Step 4: Run to verify it passes** — Run:
`uv run pytest tests/json_loader/test_rust_state_root.py::test_shim_matches_pure_python -v`
Expected: PASS.

**Step 5: Run the full differential file + a lint check**

Run:
```
uv run pytest tests/json_loader/test_rust_state_root.py -v
just static   # mypy / ruff — the new branch must type-check under strict mypy
```
Expected: all PASS; static checks clean.

**Step 6: Commit**

```bash
git add src/ethereum/state.py tests/json_loader/test_rust_state_root.py
git commit -m "feat(state): optional Rust fast path for state root computation"
```

---

## Task 6: json-loader integration with the backend active

**Files:**
- Modify: `pyproject.toml:198-201` (`[optimized]` extra)

**Step 1: Add the crate to the optimized extra.** Since `eth_trie_rs` is built
from source in-repo (not yet published), CI builds it with `maturin develop`
(Task 7); the extra documents intent for a future published wheel. Add:

```toml
optimized = [
    "rust-pyspec-glue>=0.0.9,<0.1.0",
    "ethash>=1.1.0,<2",
    # eth_trie_rs is built from rust/eth_trie_rs via maturin (see CI).
    # When published: "eth_trie_rs>=0.1.0,<0.2.0",
]
```

**Step 2: Run the existing json-loader suite with the backend present.**

Run (build first so `eth_trie_rs` is importable, then a fast subset):
```
uv run maturin develop -m rust/eth_trie_rs/Cargo.toml
uv run pytest -m "not slow" -k "genesis or state" tests/json_loader -q
```
Expected: PASS — real mainnet-shaped state roots now flow through Rust and still
match the fixtures. (This is the integration half of the parity oracle.)

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build(optimized): document eth_trie_rs under the optimized extra"
```

---

## Task 7: CI job — build the crate and run parity tests

> Use the `/edit-workflow` skill before editing `.github/workflows/test.yaml`
> (SHA-pinned actions, matrix conventions).

**Files:**
- Modify: `.github/workflows/test.yaml`

**Step 1: Add a job** that installs Rust + maturin, builds `eth_trie_rs`, and
runs the differential property test and a json-loader subset with the backend
active:

```yaml
  rust-state-root:
    name: rust-state-root parity
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<pinned-sha>   # match other jobs
      - uses: dtolnay/rust-toolchain@<pinned-sha>
        with:
          toolchain: stable
      - name: Detect Python version
        # reuse the pattern already in test.yaml
      - name: Install deps + build crate
        run: |
          uv sync
          uv run maturin develop -m rust/eth_trie_rs/Cargo.toml
      - name: Differential parity tests
        run: uv run pytest tests/json_loader/test_rust_state_root.py -v
      - name: json-loader with backend active
        run: uv run pytest -m "not slow" -k "genesis or state" tests/json_loader -q
```

**Step 2: Validate the workflow locally**

Run: `just static` (and any workflow-config validation the repo provides, e.g.
the "Validate workflow config variables" step). Confirm YAML parses and action
SHAs are pinned.
Expected: clean.

**Step 3: Commit**

```bash
git add .github/workflows/test.yaml
git commit -m "ci: build eth_trie_rs and run state-root parity tests"
```

---

## Task 8: Final verification & docs

**Files:**
- Modify: `docs/plans/2026-07-02-rust-state-root-design.md` (flip Status)
- Create: `rust/eth_trie_rs/README.md` (parity contract note)

**Step 1:** Run the whole differential file once more, plus `just static`, plus
the fallback-only path (uninstall/hide the crate) to prove the pure-Python
default still works:
```
uv run pytest tests/json_loader/test_rust_state_root.py -v
just static
uv run python -c "import sys; sys.modules['eth_trie_rs']=None; import ethereum.state as s; print(s._eth_trie_rs)"
```
Expected: tests PASS; static clean; the last prints `None` (fallback engaged).

**Step 2:** Write `rust/eth_trie_rs/README.md` documenting: the FFI contract,
the "raw preimages in, Rust secures keys" convention, the exact-pin rationale
for `alloy-trie`, and that the differential test is the source of truth.

**Step 3:** Flip the design doc Status to "Implemented".

**Step 4: Commit**

```bash
git add docs/plans/2026-07-02-rust-state-root-design.md rust/eth_trie_rs/README.md
git commit -m "docs(rust): eth_trie_rs README and design status"
```

---

## Out of scope (do NOT do here)

- PoW/ethash optimization (`ethereum_optimized/fork.py`) — legacy, pre-Merge.
- DB-backed / incremental persistent state (`state_db.py`'s larger role).
- Any change to `patricialize()` / `root()` themselves.
- Reviving or deleting `ethereum_optimized` (separate decision).
- Publishing `eth_trie_rs` to PyPI (follow-up once parity is proven in CI).
