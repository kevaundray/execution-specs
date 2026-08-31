# Refactor: Remove Explicit Stem Nodes and Use Extension Nodes (amended)

Status: implemented in `src/ethereum/binary_trie/trie.py`, 2026-07-18.
Amendments from review are marked **[amended]**.
**Superseded in part 2026-07-19**: the separate extension node was
replaced by a prefix-carrying branch — see "Fused-prefix amendment" at
the end.

## Background

The previous specification modeled a **Stem** as an explicit node in the
trie holding a fixed 256-leaf subtree, following the original Verkle stem
model. This refactor moves to the revised design discussed by Vitalik: a
**generic compressed binary radix trie** with only branch, extension, and
leaf nodes. There is no dedicated stem node type.

## Design Philosophy

The trie knows nothing about accounts, storage, code, stems, or suffixes.
It is simply `mapping<key, value>` with all semantics encoded in the keys
by the embedding (`ACCOUNT || H(address)`, `STORAGE || H(address) ||
H(address || index) || sub`, `CODE || H(code_hash || index) || sub`).

**[amended] Stems still exist — in the embedding.** The `Stem` type alias
and its documentation move to `embedding.py`. Keys sharing a stem share a
long bit prefix, so their values co-locate under one compressed subtree;
the locality property becomes emergent instead of structural.

## Node types

- `BranchNode(left, right)` — splits on one bit. Both children always
  present (a branch with an empty side compresses away), so **empty
  siblings never appear in the tree or its proofs**, and the old
  64-zero-bytes collapse rule disappears. Only the empty-tree sentinel
  (root of 32 zero bytes) survives.
- `ExtensionNode(prefix_bits, child)` — compresses a unary run. Pure
  compression, no semantics.
- `LeafNode(full_key, value)` — commits the **full key**, so a leaf's
  meaning never depends on the path taken to reach it.

## [amended] Prefix-freeness is still required

`mapping<byte_string, value>` overstates: a leaf terminates a path, so if
key A is a proper prefix of key B, B's path cannot pass through A's leaf.
Generic radix tries support arbitrary byte strings only with
value-carrying branches (MPT-style), which we do not want. The
requirement survives unchanged, now at the **key** level rather than the
stem level: no key may be a prefix of another. The embedding guarantees
it (fixed key length per zone, zones distinct in byte 0, lengths declared
as constants and asserted per derivation); the tree asserts it while
splitting keys — a key running out of bits while still grouped with
others is the violation.

## [amended] Canonical form is mandatory, and verifiers must enforce it

A commitment scheme needs one root per logical map. Rules:

1. A branch always has two non-empty subtrees.
2. An extension sits only directly above a **branch**, and is maximal
   (extensions never chain).
3. A lone key becomes a **leaf immediately** at its divergence point —
   never `Extension → Leaf`, since the leaf already carries its full key.
   (The original synopsis example had `Extension("0000000") → Leaf`,
   which violates this; corrected below.)

Proof verifiers MUST reject non-canonical structure; accepting it is a
classic soundness bug class in radix-trie proof systems.

## [amended] Commitment rules (domain-separated)

Every node type hashes behind its own tag byte, and extension prefixes
carry an explicit bit count:

```
leaf      = H(0x00 || full_key || value)
extension = H(0x01 || bit_count_2B_BE || packed_prefix_bits || child)
branch    = H(0x02 || left || right)
empty trie root = 32 zero bytes (sentinel, not a hash output)
```

Why this is mandatory, not cosmetic: without tags, a 256-bit extension
prefix plus child hash is 64 bytes — the same preimage shape as a
branch. An attacker could re-present an interior branch as an extension
whose prefix "diverges" from a victim key, yielding a verifying
exclusion proof for a key that exists. The explicit bit count prevents
`Extension("0110")` and `Extension("01100")` from packing identically.
(The old design's only type tag was the `0x00` in the stem rule; this
refactor deletes that rule, so tags must be explicit.)

Note: framing assumes byte-oriented hashing; a Poseidon field encoding
would re-open this question.

## Corrected example

Keys `S || 00000000`, `S || 00000001`, `S || 10000000`:

```
        Extension(bits of S)
                 |
              Branch            <- first suffix bit
             /      \
   Extension(000000) Leaf(S || 10000000)   <- leaf immediately, no
            |                                 extension above it
         Branch
        /      \
Leaf(S||00000000) Leaf(S||00000001)
```

## [amended] Spec vs client implementations

The insertion/split/merge/delete algorithms in the original synopsis are
**client** concerns. The spec defines the canonical committed form, and
the reference implementation rebuilds it from a flat dict on every
`root()` — canonical form comes free by construction, and no incremental
machinery is needed. Clients implementing incremental updates must take
care that delete-time merging preserves canonicity (where such bugs
live in practice). The spec still defines no deletion semantics.

## Benefits (audited)

- Removes the stem special case; the trie is a generic compressed radix
  trie, unaware of Ethereum concepts.
- Compresses the manufactured prefix chains inside `H(address)` storage
  buckets (~250 empty-sibling levels → one extension node).
- **Grinding resistance**: an attacker grinding keys to share a long
  prefix with a victim inflates the victim's proof by O(1) instead of
  O(shared bits). This reverses EIP-8297's "extension nodes not worth
  it" judgment because the bucket prefixes changed the calculus.
- Sparse stems commit through only their occupied structure (a 5-slot
  header is ~3 levels, not a fixed 8-level subtree): fewer hashes
  (modest — zero-collapse already short-circuited empties) and smaller
  witnesses (real siblings only; no zero padding).
- Future key-layout changes touch only the embedding.

## Costs the original synopsis omitted

- **Circuit shape**: fixed 256-leaf subtrees were maximally
  SNARK-friendly; variable extensions reintroduce data-dependent
  branching and variable-length hashing in-circuit.
- **Absence proofs change shape**: all absence becomes MPT-style
  exclusion (show the divergence point). The 4762-lineage gas/witness
  accounting was written against fixed stem geometry; "branch opening"
  stops being a fixed-shape unit. The gas layer can still define stems
  via the embedding (key minus final byte) — it just cannot point at a
  node type.

## Other implementation notes

- The EIP's insertion-based reference (stem-based) can no longer serve
  as the test oracle. Replaced with an insertion-based **radix**
  reference; rebuild-vs-insert agreement on every root doubles as a
  canonicity check.
- `docs/binary-trie/index.html` updated 2026-07-18 to the radix design
  (new tree section and diagram, tagged merkleization rules, radix
  decision card, refreshed EIP-relationship matrix).

## Fused-prefix amendment (2026-07-19)

Following the representation used by eth-act/zkvm-ethereum-mpt
(`crates/ref-mpt/src/trie/nodes.rs`), the separate `ExtensionNode` was
removed and the compressed prefix moved onto the branch itself — but
unlike that implementation (which must re-materialize canonical MPT encodings
at hashing time because MPT roots are consensus-fixed), the fusion
here is in the **commitment itself**:

```
leaf   = H(0x00 || full_key || value)
branch = H(0x01 || bit_count_2B_BE || packed_prefix_bits || left || right)
empty trie root = 32 zero bytes (sentinel)
```

Two node types, two tags. An ordinary branch carries `bit_count = 0`
(two bytes of overhead).

Why it dominates the three-node design here:

- **Canonical form becomes unrepresentable, not merely forbidden.**
  A branch must have two non-empty children, which forces its prefix
  to be exactly the shared run: a shorter prefix would leave every key
  agreeing on the split bit, emptying one side. There is no extension
  to chain, misplace, or leave above a leaf. The verifier obligation
  "MUST reject non-canonical structure" — the biggest soundness
  footgun of the extension design — loses its raw material.
- One hash and ~33 witness bytes saved per compressed run (extension +
  branch pair collapses to one node).
- Client algorithms simplify: an insert that splits a run truncates
  the surviving branch's prefix; a delete that collapses a branch
  concatenates the freed bits onto the survivor (nothing at all if the
  survivor is a leaf — it already carries its full key).

Costs, all minor:

- Subtree-hash stability: splitting a run changes the surviving
  branch's own hash (its prefix is in its preimage), where the
  extension design kept the branch hash stable. One extra node
  rewrite per split; relevant to client DB dedup only.
- Another divergence from the discussed three-node model to
  communicate upstream; "canonical form is unrepresentable" is the
  pitch.
- The MAX_KEY_LENGTH bound is *hard* under fusion (2026-07-19): the
  MPT never had it (RLP's variable-width framing), and a separate
  extension design could soften it via canonical chaining (greedy
  split of long runs into count-field-sized pieces — deterministic,
  hence canonical). A fused branch cannot chain — the intermediate
  branch would have one child, which is unrepresentable — so the
  count width is a structural limit; the only remedy is widening the
  field. Fine at ~124x headroom over the embedding's keys, but the
  EIP text should state it rather than let readers derive it.

Their `DigestNode` (a hash standing in for an unexpanded
subtree, with its path) is a witness/partial-trie concept — not needed
in the full-rebuild spec, but a useful preview that the fused model
extends cleanly to stateless verification.
