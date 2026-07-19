"""
The raw key/value tree underlying the [EIP-8297] Partitioned Binary
Tree, structured as a compressed binary radix trie.

TODO: 8297 still copies UBT and has stem nodes.

The tree maps variable-length keys to 32-byte values and commits to
its entire contents with a single root hash. Keys are consumed bit by
bit, most significant bit first, and must be prefix-free; see
[`Key`].

Unlike the hexary Merkle Patricia Trie it is designed to replace, the
tree uses no RLP and has no node type with Ethereum semantics: a
[`BranchNode`] splits on a single bit, an [`ExtensionNode`]
compresses a run of bits shared by every key below it, and a
[`LeafNode`] holds one key's value. The tree is kept maximally
compressed — a branch always has two subtrees, an extension appears
only above a branch, and a lone key terminates in a leaf immediately
— so one committed state has exactly one node structure and one root.

The mapping of keys to values is exposed through [`BinaryTrie`]; the
[`root`] function reduces a trie to its 32-byte commitment. The hash
function used here follows the EIP's reference implementation
(BLAKE3), but the EIP marks the final choice as open.

This module defines only the raw tree. How Ethereum state — accounts,
storage, and code — is mapped into keys and values, including the
logical **stem** grouping, is defined in
[`ethereum.binary_trie.embedding`].

[EIP-8297]: https://eips.ethereum.org/EIPS/eip-8297
[`Key`]: ref:ethereum.binary_trie.trie.Key
[`BranchNode`]: ref:ethereum.binary_trie.trie.BranchNode
[`ExtensionNode`]: ref:ethereum.binary_trie.trie.ExtensionNode
[`LeafNode`]: ref:ethereum.binary_trie.trie.LeafNode
[`BinaryTrie`]: ref:ethereum.binary_trie.trie.BinaryTrie
[`root`]: ref:ethereum.binary_trie.trie.root
[`ethereum.binary_trie.embedding`]: ref:ethereum.binary_trie.embedding
"""

# TODO: For client testing, can we for now pretend that this will be
# activated at amsterdam, to see the spec changes needed to
# accommodate this.

import copy
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Union, final

from blake3 import blake3
from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.frozen import slotted_freezable
from ethereum_types.numeric import Uint

from ethereum.crypto.hash import Hash32

EMPTY_TRIE_ROOT = Hash32(b"\x00" * 32)
"""
Root hash of an empty binary tree, defined as 32 zero bytes.

This is a sentinel value rather than a hash output: no input is
expected to hash to all zeroes, so it cannot collide with the
commitment of a non-empty tree.
"""


def blake3_hash(data: Bytes) -> Hash32:
    """
    Hash `data` with the tree's hash function.

    The EIP marks the choice of hash function as open, so it is
    isolated behind this single function. Any hash mapping bytes to
    32 bytes would do.
    """
    return Hash32(blake3(data).digest())


def bytes_to_bit_list(data: Bytes) -> Bytes:
    """
    Expand each input byte into eight bits, most significant bit first.

    Bit `d` of the result is the bit selected at depth `d` of the tree.
    """
    bit_list = bytearray(8 * len(data))
    for byte_index, byte in enumerate(data):
        for offset in range(8):
            bit_list[byte_index * 8 + offset] = (byte >> (7 - offset)) & 1
    return Bytes(bit_list)


Key = Bytes
"""
A tree key: any non-empty byte string, consumed bit by bit, most
significant bit first.

Keys must be **prefix-free**: no key may be a prefix of another. A
key is its path and a [`LeafNode`] ends a path, so a longer key
could never pass through the position a shorter key terminates at.
[`binarize`] asserts the property while splitting keys, where a
violation surfaces as a key running out of bits before separating
from its group. The key layouts actually in use, including the
logical **stem**/sub-index split, are fixed by
[`ethereum.binary_trie.embedding`].

[`LeafNode`]: ref:ethereum.binary_trie.trie.LeafNode
[`binarize`]: ref:ethereum.binary_trie.trie.binarize
[`ethereum.binary_trie.embedding`]: ref:ethereum.binary_trie.embedding
"""

LEAF_NODE_TAG = Bytes(b"\x00")
"""
First byte of every [`LeafNode`] hash preimage.

Each node type hashes behind its own tag byte so that no two node
types can share a preimage and one root commits to exactly one node
structure; see [`merkleize`].

[`LeafNode`]: ref:ethereum.binary_trie.trie.LeafNode
[`merkleize`]: ref:ethereum.binary_trie.trie.merkleize
"""

EXTENSION_NODE_TAG = Bytes(b"\x01")
"""
First byte of every [`ExtensionNode`] hash preimage.

[`ExtensionNode`]: ref:ethereum.binary_trie.trie.ExtensionNode
"""

BRANCH_NODE_TAG = Bytes(b"\x02")
"""
First byte of every [`BranchNode`] hash preimage.

[`BranchNode`]: ref:ethereum.binary_trie.trie.BranchNode
"""


@final
@slotted_freezable
@dataclass
class LeafNode:
    """
    Terminal node holding a single key's value.

    The complete key is committed, not just the bits below the
    leaf's position, so a leaf's meaning never depends on the path
    taken to reach it. A lone key becomes a leaf immediately at its
    point of divergence; an extension never sits directly above a
    leaf.
    """

    key: Key
    """
    The complete key whose value this leaf holds.
    """

    value: Bytes32
    """
    The 32-byte value stored under [`key`].

    [`key`]: ref:ethereum.binary_trie.trie.LeafNode.key
    """


@final
@slotted_freezable
@dataclass
class ExtensionNode:
    """
    Compression node standing in for a run of bits shared by every
    key in the subtree below it.

    Extensions are a pure compression device with no Ethereum
    meaning. To keep the structure canonical, an extension always
    sits directly above a [`BranchNode`] and is as long as possible:
    extensions never chain and never sit above a [`LeafNode`], which
    already carries its full key.

    [`BranchNode`]: ref:ethereum.binary_trie.trie.BranchNode
    [`LeafNode`]: ref:ethereum.binary_trie.trie.LeafNode
    """

    prefix: Bytes
    """
    The shared run of bits, one bit per byte, in consumption order.
    """

    child: "BinaryNode"
    """
    The [`BranchNode`] below the compressed run.

    [`BranchNode`]: ref:ethereum.binary_trie.trie.BranchNode
    """


@final
@slotted_freezable
@dataclass
class BranchNode:
    """
    Binary branch splitting on a single bit.

    Both subtrees are always present: a branch with an empty side
    would compress away, so empty siblings never appear in the tree
    or in its proofs.
    """

    left: "BinaryNode"
    """
    Subtree of keys whose next bit is `0`.
    """

    right: "BinaryNode"
    """
    Subtree of keys whose next bit is `1`.
    """


BinaryNode = Union[BranchNode, ExtensionNode, LeafNode]
"""
Any of the node types making up a non-empty binary tree.
"""


@final
@dataclass
class BinaryTrie:
    """
    Mapping of variable-length keys to 32-byte values with a single
    root hash that uniquely identifies its contents.

    Only the key/value pairs are stored; [`root`] rebuilds the node
    structure and rehashes it from scratch on every call, which makes
    the canonical compressed form automatic. Clients are expected to
    keep nodes and update hashes incrementally instead.

    [`root`]: ref:ethereum.binary_trie.trie.root
    """

    _data: Dict[Key, Bytes32] = field(default_factory=dict)


def copy_trie(trie: BinaryTrie) -> BinaryTrie:
    """
    Create a copy of `trie`.

    Keys and values are immutable, so the contents are shared between
    the original and the copy.
    """
    return BinaryTrie(copy.copy(trie._data))


def trie_set(trie: BinaryTrie, key: Key, value: Bytes32) -> None:
    """
    Insert or update `key` in `trie` with the given `value`.

    The caller must keep keys prefix-free; see [`Key`].

    [`Key`]: ref:ethereum.binary_trie.trie.Key
    """
    assert len(key) >= 1
    assert len(value) == 32
    trie._data[key] = value


def trie_get(trie: BinaryTrie, key: Key) -> Optional[Bytes32]:
    """
    Look up `key` in `trie`, returning `None` if absent.
    """
    return trie._data.get(key)


def encode_bit_prefix(prefix: Bytes) -> Bytes:
    """
    Encode an extension prefix for hashing: a two-byte big-endian bit
    count followed by the bits packed most significant bit first,
    zero padded to a byte boundary.

    The explicit count keeps the encoding injective: without it, two
    prefixes differing only by trailing zero bits would pack to the
    same bytes and two different trees could share a root.
    """
    packed = bytearray((len(prefix) + 7) // 8)
    for bit_index, bit in enumerate(prefix):
        packed[bit_index // 8] |= bit << (7 - bit_index % 8)
    return Bytes(len(prefix).to_bytes(2, "big") + bytes(packed))


def merkleize(node: BinaryNode) -> Hash32:
    """
    Compute the hash committing to `node` and everything below it.

    Every node type hashes behind its own tag byte, and extension
    prefixes carry an explicit bit count, so no two distinct nodes
    can share a preimage: one root commits to exactly one tree.
    """
    if isinstance(node, LeafNode):
        return blake3_hash(LEAF_NODE_TAG + node.key + node.value)
    if isinstance(node, ExtensionNode):
        return blake3_hash(
            EXTENSION_NODE_TAG
            + encode_bit_prefix(node.prefix)
            + merkleize(node.child)
        )
    return blake3_hash(
        BRANCH_NODE_TAG + merkleize(node.left) + merkleize(node.right)
    )


def binarize(entries: Mapping[Key, Bytes32], depth: Uint) -> BinaryNode:
    """
    Build the canonical node structure for `entries`, whose keys all
    share their first `depth` bits. `entries` must not be empty.

    A single entry becomes a [`LeafNode`] immediately. Multiple
    entries split at their first differing bit under a
    [`BranchNode`], wrapped in an [`ExtensionNode`] when they share
    bits beyond `depth`.

    [`LeafNode`]: ref:ethereum.binary_trie.trie.LeafNode
    [`BranchNode`]: ref:ethereum.binary_trie.trie.BranchNode
    [`ExtensionNode`]: ref:ethereum.binary_trie.trie.ExtensionNode
    """
    assert len(entries) > 0
    if len(entries) == 1:
        (key,) = entries
        return LeafNode(key, entries[key])

    bit_lists = {key: bytes_to_bit_list(key) for key in entries}

    prefix_length = Uint(0)
    while True:
        position = depth + prefix_length
        # A key running out of bits while still grouped with others
        # would be a prefix of theirs; see `Key`.
        for bit_list in bit_lists.values():
            assert position < Uint(len(bit_list))
        bits_at_position = {
            bit_list[position] for bit_list in bit_lists.values()
        }
        if len(bits_at_position) > 1:
            break
        prefix_length += Uint(1)

    split = depth + prefix_length
    left = {
        key: value
        for key, value in entries.items()
        if bit_lists[key][split] == 0
    }
    right = {
        key: value
        for key, value in entries.items()
        if bit_lists[key][split] == 1
    }
    branch = BranchNode(
        binarize(left, split + Uint(1)),
        binarize(right, split + Uint(1)),
    )
    if prefix_length == Uint(0):
        return branch
    shared_bits = next(iter(bit_lists.values()))
    return ExtensionNode(Bytes(shared_bits[depth:split]), branch)


def root(trie: BinaryTrie) -> Hash32:
    """
    Compute the root hash of `trie`.

    An empty trie commits to [`EMPTY_TRIE_ROOT`]; any other trie
    commits to the hash of its canonical node structure.

    [`EMPTY_TRIE_ROOT`]: ref:ethereum.binary_trie.trie.EMPTY_TRIE_ROOT
    """
    if len(trie._data) == 0:
        return EMPTY_TRIE_ROOT
    return merkleize(binarize(trie._data, Uint(0)))
