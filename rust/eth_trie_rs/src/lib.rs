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

/// Compute a secured-trie root from (hashed_key, rlp_value) pairs.
/// Keys must already be keccak256-hashed (both call sites hash the raw
/// preimage); this function only sorts them and builds the trie.
fn secured_root(mut leaves: Vec<(B256, Vec<u8>)>) -> B256 {
    leaves.sort_by(|a, b| a.0.cmp(&b.0));
    let mut hb = HashBuilder::default();
    for (hashed_key, value) in leaves {
        hb.add_leaf(Nibbles::unpack(hashed_key), &value);
    }
    hb.root()
}

/// One account as marshalled by the Python shim in `ethereum/state.py`.
///
/// A 4-tuple of byte strings; every integer is **minimal big-endian** (no
/// leading zero bytes), exactly what `int.to_bytes()` produces on the spec
/// side. Fields, in order:
///
/// 0. `address`   — 20-byte account address, a **raw preimage**; this crate
///    keccak256-hashes it to place the account in the secured state trie.
/// 1. `nonce`     — account nonce, ≤ 8 bytes → parsed to `u64` (an oversized
///    nonce raises `OverflowError`; see [`u64_from_be`]).
/// 2. `balance`   — account balance in wei, ≤ 32 bytes → parsed to `U256`.
/// 3. `code_hash` — 32-byte keccak256 of the account's bytecode.
type AccountInput = (Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>);

/// One storage slot as a `(key, value)` pair of byte strings:
///
/// - `key`   — 32-byte storage slot key, a **raw preimage**; hashed here to
///   place the slot in the account's secured storage trie.
/// - `value` — slot value, ≤ 32 bytes minimal big-endian → parsed to `U256`
///   and RLP-encoded.
type StorageSlot = (Vec<u8>, Vec<u8>);

/// Storage tries keyed by account address — the *same* 20-byte preimage used
/// in [`AccountInput`] field 0. Accounts with empty storage are simply absent
/// from the map (the spec deletes emptied storage tries), and get
/// `EMPTY_ROOT_HASH` as their storage root.
type StorageInput = HashMap<Vec<u8>, Vec<StorageSlot>>;

/// Compute the Ethereum state root for a full account/storage snapshot.
///
/// This is the fast-path equivalent of the spec's
/// `State.compute_state_root_and_trie_changes`: it builds each account's
/// storage-trie root, RLP-encodes the account as `[nonce, balance,
/// storage_root, code_hash]` ([`TrieAccount`]), and returns the secured
/// main-trie root as 32 bytes.
///
/// **Trusted-caller length contract** (the Python shim always upholds it):
/// address = 20 bytes, code hash & storage keys = 32 bytes, balance & storage
/// values ≤ 32 bytes. Violations **panic** via `from_slice`/`from_be_slice` —
/// acceptable for this internal v1 boundary, to be hardened into `PyErr`
/// before any untrusted caller. The nonce is the one exception: values that
/// exceed `u64` are reachable from valid states (pre-EIP-2681), so they raise
/// a catchable `OverflowError` and the shim falls back to pure Python instead
/// of panicking.
#[pyfunction]
fn state_root(
    py: Python<'_>,
    accounts: Vec<AccountInput>,
    storage: StorageInput,
) -> PyResult<Py<PyBytes>> {
    let mut account_leaves: Vec<(B256, Vec<u8>)> = Vec::with_capacity(accounts.len());

    for (addr, nonce, balance, code_hash) in accounts {
        let storage_root = match storage.get(&addr) {
            Some(slots) if !slots.is_empty() => {
                let leaves = slots
                    .iter()
                    .map(|(k, v)| {
                        let val = U256::from_be_slice(v);
                        (keccak256(k), alloy_rlp::encode(val))
                    })
                    .collect();
                secured_root(leaves)
            }
            _ => alloy_trie::EMPTY_ROOT_HASH,
        };

        let account = TrieAccount {
            nonce: u64_from_be(&nonce)?,
            balance: U256::from_be_slice(&balance),
            storage_root,
            code_hash: B256::from_slice(&code_hash),
        };
        account_leaves.push((keccak256(&addr), alloy_rlp::encode(account)));
    }

    let root = secured_root(account_leaves);
    Ok(PyBytes::new_bound(py, root.as_slice()).unbind())
}

/// Parse a minimal big-endian byte string into a `u64`.
///
/// Ethereum account nonces are capped below 2**64 by EIP-2681, but the spec's
/// `Uint` nonce is unbounded and pre-EIP-2681 forks enforce no cap, so
/// out-of-range values are reachable from valid states. `nonce.to_be_bytes()`
/// is a minimal big-endian encoding, so a length above 8 means the value does
/// not fit; raise a catchable `OverflowError` (never truncate, which would
/// yield a wrong, consensus-divergent state root) so the Python shim can fall
/// back to the pure-Python reference.
fn u64_from_be(bytes: &[u8]) -> PyResult<u64> {
    if bytes.len() > 8 {
        return Err(pyo3::exceptions::PyOverflowError::new_err(
            "nonce exceeds u64",
        ));
    }
    let mut buf = [0u8; 8];
    buf[8 - bytes.len()..].copy_from_slice(bytes);
    Ok(u64::from_be_bytes(buf))
}

#[pymodule]
fn eth_trie_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(state_root, m)?)?;
    Ok(())
}
