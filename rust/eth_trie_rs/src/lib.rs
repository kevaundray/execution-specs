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
    // Length contract (callers are trusted — the Python shim in state.py):
    // addr = 20 bytes, code_hash & storage keys = 32 bytes, balance/storage
    // values = <=32 bytes big-endian, nonce fits in u64. Malformed lengths
    // will panic (from_slice/from_be_slice); acceptable for this internal v1
    // boundary, to be hardened to PyErr before any untrusted caller.
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

/// Parse a minimal big-endian byte string into a `u64`.
///
/// Ethereum account nonces are capped below 2**64 by EIP-2681, so the spec's
/// unbounded `Uint` nonce always fits in a `u64`. `nonce.to_be_bytes()` is a
/// minimal big-endian encoding, so a length above 8 means the value is out of
/// contract; we panic rather than silently truncate (which would yield a wrong,
/// consensus-divergent state root).
fn u64_from_be(bytes: &[u8]) -> u64 {
    assert!(
        bytes.len() <= 8,
        "nonce exceeds u64 (violates EIP-2681 nonce cap)"
    );
    let mut buf = [0u8; 8];
    buf[8 - bytes.len()..].copy_from_slice(bytes);
    u64::from_be_bytes(buf)
}

#[pymodule]
fn eth_trie_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(state_root, m)?)?;
    Ok(())
}
