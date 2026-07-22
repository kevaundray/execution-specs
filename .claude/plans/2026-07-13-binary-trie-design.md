# EIP-8297 Partitioned Binary Tree — Design

Date: 2026-07-13
Branch: `projects/binary-trie`
Spec: https://eips.ethereum.org/EIPS/eip-8297

## Goal

Add the EIP-8297 partitioned binary tree to the shared (non-fork) spec code,
plus unit tests. This mirrors the existing shared
`src/ethereum/merkle_patricia_trie.py` — the binary trie is not fork wiring,
so it does not live under `src/ethereum/forks/`.

## Scope

Everything in EIP-8297 that is pure data structure and key derivation:

- Tree structure: `StemNode`, `InternalNode` (empty subtree = `None`).
- Merkleization (BLAKE3, `hash(64 zero bytes) = 32 zero bytes`, stem node
  hash = `hash(stem || 0x00 || subtree_root)`).
- Bit helpers (`bytes_to_bits`, `bits_to_bytes`, MSB-first).
- Zones and stems: `zone_stem`, `storage_stem`, `key_hash`.
- Tree embedding: `get_tree_key_for_header` / `basic_data` / `code_hash` /
  `storage_slot` / `code_chunk`, `Address32`, `address20_to_address32`.
- Code chunking: `chunkify_code`.
- Basic-data leaf packing (version / code_size / nonce / balance).

Out of scope: fork integration (EIP-7612 overlay), access events (EIP-4762
adaptation), gas.

## Decisions

- **Location**: the `src/ethereum/binary_trie/` package, split into
  `trie.py` (the raw key/value tree: nodes, merkleization, root) and
  `embedding.py` (the state-embedding layer on top: zones, tree-key
  derivation, code chunking, basic-data packing). Registered in
  `[tool.setuptools] packages`. Tests mirror the split:
  `tests/test_binary_trie.py` and `tests/test_binary_trie_embedding.py`.
- **Hash**: add the `blake3` package as a dependency; keep the hash behind a
  single internal function since the EIP marks the hash choice as not final.
- **API style**: mirror the repo MPT style rather than the EIP's mutating
  reference implementation — a dict-backed `BinaryTrie` dataclass with
  `trie_set` / `trie_get` / `copy_trie`, and a `root()` that builds the node
  structure on demand via `binarize(...)`, the binary analogue of
  `patricialize(...)`. `binarize` partitions stems by the bit at the current
  depth: empty → `None`, one stem → `StemNode`, else `InternalNode`. This
  yields the same minimal-internal-node tree as the EIP's insertion
  algorithm.
- **No deletion semantics**: the EIP only inserts/overwrites. A 32-byte zero
  value is *not* absence (they merkleize differently).

## Radix refactor (2026-07-18, after the variable-key pivot)

Stem nodes removed; the tree is now a generic compressed binary radix
trie (branch / extension / leaf) with domain-tagged hashing. Full
amended design, rationale, and soundness analysis in
`2026-07-18-radix-refactor.md`. Key implementation facts:

- `trie.py`: `LeafNode(full_key, value)` and `BranchNode(prefix_bits,
  left, right)` — the prefix is fused onto the branch (2026-07-19
  amendment, after eth-act/zkvm-ethereum-mpt); no extension node. Tags
  `0x00` (leaf) / `0x01` (branch); branch preimage carries a 2-byte BE
  bit count + packed prefix bits. Canonical form is unrepresentable to
  violate (two-non-empty-children forces maximal prefixes; leaves
  carry full keys); prefix-freeness is a key-level property asserted
  during the shared-prefix scan.
- `StemNode`, `InternalNode`, `StemValues`, `merkle_hash`, and the
  zero-collapse rule are gone; `EMPTY_TRIE_ROOT` survives as the
  empty-trie sentinel handled in `root()`.
- `Stem` alias moved to `embedding.py` (logical concept only).
- `embedding.py` otherwise untouched; embedding tests unchanged.
- Test oracle replaced: insertion-based radix reference; agreement
  with the rebuild-based spec doubles as a canonicity check. A
  hand-computed canonical-form example test pins the
  no-extension-above-leaf rule.
- `docs/binary-trie/index.html` updated to the radix design the same
  day.

## Variable-key pivot (2026-07-18)

The implementation switched from EIP-8297's fixed 32-byte keys to the
variable-length key scheme of EIP-7864 post-PR-11832
(https://github.com/ethereum/EIPs/pull/11832), keeping 8297's good parts
where orthogonal:

- **Key shape**: `zone_byte ‖ digest(s) ‖ sub_index`, prefix-free by
  same-length-per-zone. Header and code keys are 34 bytes
  (`zone ‖ H(x) ‖ sub`); storage keys are 66 bytes
  (`0xFF ‖ H(addr) ‖ H(addr ‖ tree_index) ‖ sub`). Digests untruncated.
- **Zones**: full byte — account `0`, code `1`, storage `255`, `2`–`254`
  reserved. `ZONE_BITS`, `STORAGE_ZONE_BIT`, and the 60/187-bit storage
  split are gone, as are the bit-list packing helpers in the embedding
  (`bit_list_to_bytes` deleted from `trie.py`; `bytes_to_bit_list`
  survives for `binarize`).
- **Kept from 8297** (deliberately, differs from the 7864 PR):
  content-addressed overflow code (`code_hash`-keyed, dedup preserved)
  and the 64-slot/128-chunk header layout.
- **Prefix-freeness**: `trie_set` requires `len(key) >= 2` and documents
  the caller obligation; `root()`/`binarize` asserts
  `depth < 8 * len(stem)` while splitting, which detects any stem that
  is a prefix of another. `merkle_hash` accepts variable-length stem
  preimages (`len >= 32`).
- Tests updated to byte-level vectors; new tree tests cover mixed
  34/66-byte keys against the reference port and prefix-violation
  detection.
- Note for upstream: hash-input framing (stem preimages now vary in
  length) and the long internal chains inside `H(addr)` storage buckets
  (~250 empty-sibling levels) are open design points to raise. (Both
  were later answered in-implementation by the radix refactor — tagged
  hashing and branch-prefix compression — but the answers themselves
  need upstreaming.)
- Note for upstream — salt/compression overlap (2026-07-19): the
  storage suffix salt (`H(address ‖ tree_index)` rather than
  `H(tree_index)`) exists to make ground slot clusters
  non-transferable between contracts — the 7864 rewrite's stated DoS
  rationale. Under the stem-node design that attack inflated proofs by
  O(shared bits) via empty-sibling chains; branch-prefix compression
  caps it at O(1) per neighbor, with in-bucket proof depth growing only
  ~log(groups). The two mitigations now overlap: the salt is
  defense-in-depth rather than the primary wall. Keep it — it costs
  one hash at key-derivation time and still stops grinding reuse for
  the residual log-factor effects — but the EIP text should present
  the two mechanisms together rather than let the salt carry the whole
  DoS argument.
- Divergences from the PR text to re-check once upstream pins test
  vectors (audited 2026-07-18):
  - **Group-index encoding**: the PR hashes
    `address + int_to_bytes(high)` for storage (helper undefined) and
    `address + bytes([high])` for code (single byte, breaks at group
    256). We use `tree_index.to_be_bytes32()` (32-byte big-endian,
    inherited from 8297) in both. Same structure, different bytes, so
    keys will not match the PR's until encodings are pinned.
  - **Storage overflow numbering**: the PR subtracts
    `STORAGE_CHUNKS_IN_HEADER` before the `high`/`low` split; we keep
    8297's `slot // 256` mapping (header carve-out for slots < 64,
    slot 64 lands at tree_index 0 / sub-index 64), matching 8297's
    published test vectors.
  - Upstream bugs found while auditing (worth reporting on the PR):
    undefined `STORAGE_OFFSET`/`chunk_id`/`int_to_bytes` names,
    `MAIN_STORAGE_OFFSET` left in the parameter table but unused, and
    the invariant line `STEM_SUBTREE_WIDTH > CODE_OFFSET >
    HEADER_STORAGE_OFFSET` now false under the new table (256 > 4 >
    20). The PR also deleted the depth assert without replacing it, so
    its reference never detects prefix violations; ours does.
- `docs/binary-trie/index.html` updated 2026-07-18 to describe the
  variant (key shapes, zone byte table, adoption matrix vs the two EIPs).

## Follow-ups from TODO review (2026-07-19)

- **Companion EIPs needed at fork time**: the snap protocol is
  MPT-shaped (account/storage ranges keyed by the old layout) and needs
  a successor version for the single zoned tree; `eth_getProof`
  (EIP-1186, Stagnant) returns MPT-format proofs and must be replaced
  by a new method — the `eth_` namespace has no per-method versioning
  (unlike `engine_*Vn`), so evolution means a new method name in a new
  EIP. The proof-format RPC is the same design artifact as the witness
  format (where eth-act's `DigestNode` concept lives).
- **`MAX_KEY_LENGTH = 8192`**: the two-byte bit count in the branch
  preimage caps representable shared prefixes, which implies a maximum
  key length; now enforced unconditionally in `trie_set` (stated
  contract) with a backstop assert in `encode_bit_prefix`, instead of a
  data-dependent failure when two long keys happen to share a prefix.
- **`get_tree_key(zone, tree_position, sub_index)`**: `zone_stem` was
  merged upward into a single key constructor mirroring the 7864
  rewrite's pseudocode shape; `Zone` and the sub-index parameter are
  typed `U8`, so byte-width violations fail at construction.
  `storage_stem` became `storage_tree_position` (digests only).
- **Considered and rejected**: an XOR-based divergence scan for
  `binarize` (elegant for two equal-length ints, alignment gymnastics
  for groups of variable-length keys) and an XOR-based second oracle
  (little value over the existing insertion-based reference).
- **Considered and rejected (2026-07-19): varint prefix count.** A
  continuation-byte encoding for the branch prefix count would lift
  the MAX_KEY_LENGTH bound, but: (a) varints admit non-minimal
  encodings, so the spec would need a minimal-encoding rule and
  verifiers a MUST-reject check — re-importing at the byte level the
  canonicity obligation the fused design eliminated structurally, a
  class with real consensus-bug history; (b) it makes branch
  preimages variable-width at the front, restoring data-dependent
  offsets ("RLP-lite") that the framing design deliberately escaped;
  (c) it cannot be deferred — any count-encoding change re-hashes
  every branch and re-roots every tree, so the choice is baked into
  the format now regardless. If the bound ever binds, a wider fixed
  field (3–4 bytes) strictly dominates: flat cost, zero canonicity
  rules, same re-rooting fork either way.
- **Open (from code notes)**: where per-key metadata (hot/cold,
  expiry epochs) lives — via reserved sub-indices (the pattern the
  header already uses for basic data) or value-carrying branch nodes
  (rejected earlier: reintroduces the MPT's 17th-slot tax and
  non-prefix-free keys). Current position: reserved sub-indices.

## Open questions (from code review, 2026-07-15)

Parked here from TODO comments; none are resolvable inside the port itself.

1. **Amsterdam activation spike**: pretend EIP-8297 activates at Amsterdam
   to discover the fork-integration deltas (state.py trie swap, no
   `storage_root`, code chunks written at deploy, `EXTCODE*` reading through
   the embedding). Gas/witness rules explicitly out of scope. Wants its own
   branch off `projects/binary-trie`.
2. **Storage-zone critique** — RESOLVED by the variable-key pivot
   (2026-07-18): zones are now a full byte and label width no longer
   competes with hash bits, so the critique is moot in this codebase.
3. **Header co-location data (upstream)**: back the 64-slot / 128-chunk
   header split with mainnet access statistics (the Verkle-era gas analyses
   measured chunk access patterns).
4. **Empty-code accounts** — RESOLVED: the code-hash leaf holds
   `keccak("")` (the classic empty-code hash) for codeless accounts.
   - The leaf is *written*, not absent: EIP-8297 (inherited verbatim from
     EIP-6800/7864) states "`code_hash` and `code_size` are set on contract
     or EOA creation."
   - The value must be `keccak("")`, not zero and not `blake3("")`:
     `EXTCODEHASH` of an existing codeless account must keep returning
     `keccak("")`, and the leaf is what backs that answer. The tree's
     internal hash (BLAKE3, still open) is irrelevant here — `code_hash` is
     an EVM-observable *value* stored in a leaf, not a tree commitment.
   - The EIP-7748 migration copies MPT accounts, whose codeless
     `code_hash` is already `keccak("")`, so migrated and newly created
     accounts agree.
   - `chunkify_code(b"") == []` stays correct: no chunk leaves, and
     `code_size = 0` in basic data.
   - Test coverage for the compat claim:
     `tests/constantinople/eip1052_extcodehash/test_extcodehash.py`
     (`valid_from("ConstantinopleFix")`, no upper bound) asserts
     `keccak256(b"")` for existing codeless accounts and `0` for
     nonexistent ones; it re-fills automatically once a binary-trie fork
     exists. At integration, add the leaf-level unit test:
     `trie_get(trie, get_tree_key_for_code_hash(addr)) ==
     EMPTY_CODE_HASH` (constant at `src/ethereum/state.py`). Note the
     existing/nonexistent distinction means account existence must be
     re-derived from leaf presence in the new tree.
5. **Zone as enum**: a `Zone` enum (repo precedent: `Ops`) would catch
   misuse of arbitrary `Uint`s, but reserved zones 2–7 are legitimate
   values and the tests exercise all 16; kept as `Uint` constants for now.
6. **Variable-width stem occupancy**: committing only to occupied leaf
   ranges would cut hashing but changes the commitment — a consensus-level
   proposal for the EIP, not a port optimization. (Zero-collapse already
   makes empty leaves nearly free to hash.)

## Tests (`tests/test_binary_trie.py`)

1. **Cross-check vs EIP reference**: port the EIP's insertion-based
   `BinaryTree` pseudocode verbatim as a test helper; assert equal roots on
   randomized key/value sets.
2. Structural: empty root (all zeros), single stem, same-stem multi-value,
   long shared stem prefixes (deep splits), storage zone at depth 1, order
   independence, zero-value ≠ absent.
3. EIP "Test Cases" key-derivation vectors, bit-for-bit.
4. `chunkify_code`: EIP's PUSH4-spanning example, padding, PUSH32 at chunk
   boundary, pushdata running past end of code, empty code.
5. Basic-data packing offsets.
