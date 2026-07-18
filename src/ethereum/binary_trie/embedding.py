"""
The [EIP-8297] embedding of Ethereum state into the binary tree.

Account and storage tries are merged into the single key/value tree
defined in [`ethereum.binary_trie.trie`], which also holds contract
code. This module defines how accounts, storage slots, and code
chunks are assigned keys and packed into values.

The first byte of every key is a **zone** identifier that labels the
category of state the key holds. Account headers live in
[`ACCOUNT_ZONE`], content-addressed overflow code in [`CODE_ZONE`],
and overflow storage in [`STORAGE_ZONE`]. Keys are variable length —
a zone byte, one or two full 32-byte digests, and a final sub-index
byte — so digests are used untruncated, and every key of a zone has
the same length, keeping stems prefix-free as the tree requires.
Data accessed together is co-located in one stem to reduce branch
openings: the account header stem holds an account's basic data,
code hash, first storage slots, and first code chunks.

State is embedded into the key space through the derivation functions
[`get_tree_key_for_basic_data`], [`get_tree_key_for_code_hash`],
[`get_tree_key_for_storage_slot`], and [`get_tree_key_for_code_chunk`],
with contract code split into leaf values by [`chunkify_code`] and an
account's scalar fields packed into one leaf by [`encode_basic_data`].

[EIP-8297]: https://eips.ethereum.org/EIPS/eip-8297
[`ethereum.binary_trie.trie`]: ref:ethereum.binary_trie.trie
[`ACCOUNT_ZONE`]: ref:ethereum.binary_trie.embedding.ACCOUNT_ZONE
[`CODE_ZONE`]: ref:ethereum.binary_trie.embedding.CODE_ZONE
[`STORAGE_ZONE`]: ref:ethereum.binary_trie.embedding.STORAGE_ZONE
[`chunkify_code`]: ref:ethereum.binary_trie.embedding.chunkify_code
[`encode_basic_data`]: ref:ethereum.binary_trie.embedding.encode_basic_data
[`get_tree_key_for_basic_data`]: ref:ethereum.binary_trie.embedding.get_tree_key_for_basic_data
[`get_tree_key_for_code_hash`]: ref:ethereum.binary_trie.embedding.get_tree_key_for_code_hash
[`get_tree_key_for_storage_slot`]: ref:ethereum.binary_trie.embedding.get_tree_key_for_storage_slot
[`get_tree_key_for_code_chunk`]: ref:ethereum.binary_trie.embedding.get_tree_key_for_code_chunk
"""  # noqa: E501

from typing import List

from ethereum_types.bytes import Bytes, Bytes20, Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import Hash32, keccak256
from ethereum.utils.byte import left_pad_zero_bytes

from .trie import Key, blake3_hash

Stem = Bytes
"""
All bytes of a [`Key`] except the final sub-index byte.

The stem is purely an embedding concept: the tree has no stem node
type, but keys sharing a stem share a long bit prefix, so their
values co-locate under one compressed subtree and open together in
one branch.

[`Key`]: ref:ethereum.binary_trie.trie.Key
"""

Zone = Uint
"""
One-byte identifier labeling the category of state a key holds,
prepended as the first byte of every [`Stem`].

Defined zones are [`ACCOUNT_ZONE`], [`CODE_ZONE`], and
[`STORAGE_ZONE`]; the remaining values are reserved for future state
categories. Because the tree consumes key bits most significant
first, every zone is a subtree rooted eight levels below the tree's
root.

[`Stem`]: ref:ethereum.binary_trie.embedding.Stem
[`ACCOUNT_ZONE`]: ref:ethereum.binary_trie.embedding.ACCOUNT_ZONE
[`CODE_ZONE`]: ref:ethereum.binary_trie.embedding.CODE_ZONE
[`STORAGE_ZONE`]: ref:ethereum.binary_trie.embedding.STORAGE_ZONE
"""

Address32 = Bytes32
"""
32-byte address used to key the tree.

Legacy 20-byte addresses are converted by [`address20_to_address32`].

[`address20_to_address32`]: ref:ethereum.binary_trie.embedding.address20_to_address32
"""  # noqa: E501

BASIC_DATA_LEAF_KEY = Uint(0)
"""
Sub-index of the account header leaf packing version, code size,
nonce, and balance.
"""

BASIC_DATA_VERSION = Uint(0)
"""
Version of the basic data leaf layout, packed as the leaf's first
byte by [`encode_basic_data`]. A future change to the layout bumps
the version so readers can tell the encodings apart.

[`encode_basic_data`]: ref:ethereum.binary_trie.embedding.encode_basic_data
"""

CODE_HASH_LEAF_KEY = Uint(1)
"""
Sub-index of the account header leaf holding the code hash.
"""

EMPTY_CODE_HASH = keccak256(b"")
"""
Code hash for accounts without code.

The leaf is written on account creation; EOAs included and holds
the Keccak hash of empty bytecode: `EXTCODEHASH` of an existing
codeless account must keep returning this value, and the [EIP-7748]
migration copies it unchanged from the frozen Merkle Patricia Trie.

The tree's own hash function plays no part here: `code_hash` is an
EVM-observable value stored in a leaf, not a tree commitment, so it
stays Keccak even though the tree hashes with [`blake3_hash`].

[EIP-7748]: https://eips.ethereum.org/EIPS/eip-7748
[`blake3_hash`]: ref:ethereum.binary_trie.trie.blake3_hash
"""

HEADER_STORAGE_OFFSET = Uint(64)
"""
Sub-index of storage slot `0` within the account header stem. Slots
`0` through `63` live in the header.
"""

CODE_OFFSET = Uint(128)
"""
Sub-index of code chunk `0` within the account header stem. Chunks
`0` through `127` live in the header.
"""

STEM_SUBTREE_WIDTH = Uint(256)
"""
Number of values grouped under a single stem.
"""

ACCOUNT_ZONE = Zone(0)
"""
Zone byte of account header stems.
"""

CODE_ZONE = Zone(1)
"""
Zone byte of content-addressed overflow code stems.
"""

STORAGE_ZONE = Zone(255)
"""
Zone byte of overflow storage stems.

Storage sits at the far end of the zone byte, leaving zones `2`
through `254` reserved for future state categories. Because keys are
variable length, a zone's one-byte label says nothing about its
capacity: every zone's key space is unbounded behind its prefix.
"""

ACCOUNT_KEY_LENGTH = Uint(34)
"""
Length of every account zone key: the zone byte, a full address
digest, and the sub-index byte.

Prefix-freeness rests on two facts, each enforced separately: within
a zone, one fixed length makes proper prefixes impossible, and
across zones, keys already differ in their zone byte. Zones sharing
a length, as the account and code zones do, is therefore harmless.
Each derivation function asserts the length of the key it
constructs.
"""

CODE_KEY_LENGTH = Uint(34)
"""
Length of every code zone key: the zone byte, a full digest of the
code hash and group index, and the sub-index byte.
"""

STORAGE_KEY_LENGTH = Uint(66)
"""
Length of every storage zone key: the zone byte, two full digests
binding the account and its group index, and the sub-index byte.
"""

PUSH_OFFSET = Uint(95)
"""
Opcode value one below `PUSH1`, so `PUSH_OFFSET + n` is the opcode
pushing `n` bytes.
"""

PUSH1 = PUSH_OFFSET + Uint(1)
"""
Opcode of the smallest push instruction.
"""

PUSH32 = PUSH_OFFSET + Uint(32)
"""
Opcode of the largest push instruction.
"""


def address20_to_address32(address: Bytes20) -> Address32:
    """
    Convert a legacy 20-byte address by prepending 12 zero bytes.

    The embedding keys the tree by 32-byte addresses so that a future
    address-space extension needs no re-keying; legacy addresses
    occupy the zero-padded corner of that space.
    """
    return Address32(left_pad_zero_bytes(address, 32))


def key_hash(data: Bytes) -> Hash32:
    """
    Hash `data` for use in tree key derivation.
    """
    return blake3_hash(data)


def zone_stem(zone: Zone, digest: Bytes) -> Stem:
    """
    Build a stem from a one-byte `zone` identifier followed by the
    whole of `digest`.
    """
    assert zone < Zone(256)  # The zone identifier is a single byte.
    return Stem(bytes([int(zone)]) + digest)


def get_tree_key_for_header(address: Address32, sub_index: Uint) -> Key:
    """
    Compute the key of the account header leaf at `sub_index`.

    The header stem is in [`ACCOUNT_ZONE`] and is keyed by the address
    alone, so each account has exactly one header stem.

    [`ACCOUNT_ZONE`]: ref:ethereum.binary_trie.embedding.ACCOUNT_ZONE
    """
    stem = zone_stem(ACCOUNT_ZONE, key_hash(address))
    key = Key(stem + bytes([int(sub_index)]))
    assert len(key) == int(ACCOUNT_KEY_LENGTH)
    return key


def get_tree_key_for_basic_data(address: Address32) -> Key:
    """
    Compute the key of the account's basic data leaf.
    """
    return get_tree_key_for_header(address, BASIC_DATA_LEAF_KEY)


def get_tree_key_for_code_hash(address: Address32) -> Key:
    """
    Compute the key of the account's code hash leaf.
    """
    return get_tree_key_for_header(address, CODE_HASH_LEAF_KEY)


def storage_stem(address: Address32, tree_index: U256) -> Stem:
    """
    Build the stem of an account's overflow storage group at
    `tree_index`.

    The stem carries two full digests after the zone byte:
    `key_hash(address)`, which gathers all of an account's overflow
    storage under one subtree — the unit that expiry and sync schemes
    operate on — and `key_hash(address ‖ tree_index)`, which spreads
    the account's groups within that subtree. Binding both digests to
    the address stops storage keys ground to sit close together from
    being reused by a different contract.
    """
    prefix = key_hash(address)
    suffix = key_hash(address + tree_index.to_be_bytes32())
    return zone_stem(STORAGE_ZONE, prefix + suffix)


def get_tree_key_for_storage_slot(
    address: Address32, storage_key: U256
) -> Key:
    """
    Compute the key of a storage slot.

    Slots `0` through `63` live in the account header stem; all other
    slots live in the storage zone, grouped 256 to a stem. The lowest
    slots are the hottest — compilers allocate scalar fields from
    slot `0` — so keeping them in the header lets a transaction open
    one branch for an account's basic data and hot storage together.
    # TODO: still need to check why first 64
    """
    if storage_key < U256(CODE_OFFSET - HEADER_STORAGE_OFFSET):
        return get_tree_key_for_header(
            address, HEADER_STORAGE_OFFSET + Uint(storage_key)
        )
    tree_index = storage_key // U256(STEM_SUBTREE_WIDTH)
    sub_index = storage_key % U256(STEM_SUBTREE_WIDTH)
    key = Key(storage_stem(address, tree_index) + bytes([int(sub_index)]))
    assert len(key) == int(STORAGE_KEY_LENGTH)
    return key


def get_tree_key_for_code_chunk(
    address: Address32, code_hash: Hash32, chunk_id: Uint
) -> Key:
    """
    Compute the key of a code chunk.

    Chunks `0` through `127` live in the account header stem: the
    start of a contract's code — dispatchers and entry points — is
    its most executed region, so the first chunks open with the same
    branch as the account's basic data. Chunks at index `128` and
    above live in [`CODE_ZONE`], content-addressed by `code_hash` so
    contracts with identical bytecode share leaves.

    `code_hash` is the account's Keccak code hash. The same value
    stored in the code hash leaf, kept Keccak so `EXTCODEHASH` is
    unchanged. [`key_hash`] only spreads the stem within the zone;
    the tree's hash choice never alters which code shares leaves.

    #TODO: need to justify why 128

    There is no separate code commitment: every chunk is an ordinary
    leaf under the state root, so proving one chunk takes a single
    branch and never requires the rest of the code.

    [`CODE_ZONE`]: ref:ethereum.binary_trie.embedding.CODE_ZONE
    [`key_hash`]: ref:ethereum.binary_trie.embedding.key_hash
    """
    if chunk_id < STEM_SUBTREE_WIDTH - CODE_OFFSET:
        return get_tree_key_for_header(address, CODE_OFFSET + chunk_id)
    overflow = chunk_id - (STEM_SUBTREE_WIDTH - CODE_OFFSET)
    tree_index = overflow // STEM_SUBTREE_WIDTH
    sub_index = overflow % STEM_SUBTREE_WIDTH
    stem = zone_stem(
        CODE_ZONE, key_hash(code_hash + tree_index.to_be_bytes32())
    )
    key = Key(stem + bytes([int(sub_index)]))
    assert len(key) == int(CODE_KEY_LENGTH)
    return key


def chunkify_code(code: Bytes) -> List[Bytes32]:
    """
    Split `code` into the 32-byte chunks stored in the tree.

    Chunk `i` holds the `i`-th 31-byte slice of the code in bytes `1`
    through `31`, preceded by one byte counting how many of the
    slice's leading bytes are data of a push instruction that began in
    an earlier chunk. The count lets a chunk be interpreted without
    its predecessors and is capped at `31`, the chunk payload size.
    """
    if len(code) % 31 != 0:
        code = Bytes(code + b"\x00" * (31 - len(code) % 31))  # Padding

    # Number of push-data bytes remaining at each position, counting
    # the position itself; `0` marks executable bytes. The extra 32
    # entries let the largest push record data past the end of the
    # code.
    remaining_push_data = [0] * (len(code) + 32)
    position = 0
    while position < len(code):
        opcode = Uint(code[position])
        if PUSH1 <= opcode <= PUSH32:
            push_data_bytes = int(opcode - PUSH_OFFSET)
        else:
            push_data_bytes = 0
        position += 1
        for offset in range(push_data_bytes):
            remaining_push_data[position + offset] = push_data_bytes - offset
        position += push_data_bytes

    return [
        Bytes32(
            bytes([min(remaining_push_data[start], 31)])
            + code[start : start + 31]
        )
        for start in range(0, len(code), 31)
    ]


def encode_basic_data(code_size: Uint, nonce: Uint, balance: U256) -> Bytes32:
    """
    Pack an account's basic data into the 32-byte value stored at
    [`BASIC_DATA_LEAF_KEY`].

    The fields are packed big-endian: a version byte of zero, three
    reserved zero bytes, four bytes of code size, eight bytes of
    nonce, and sixteen bytes of balance.

    [`BASIC_DATA_LEAF_KEY`]: ref:ethereum.binary_trie.embedding.BASIC_DATA_LEAF_KEY
    """  # noqa: E501
    return Bytes32(
        bytes([int(BASIC_DATA_VERSION)])
        + b"\x00" * 3  # TODO: double check this -- Reserved bytes.
        + code_size.to_be_bytes4()
        + nonce.to_be_bytes8()
        + int(balance).to_bytes(16, "big")
    )
