"""
The raw key/value tree underlying the [EIP-8297] Partitioned Binary
Tree.

The tree maps variable-length keys to 32-byte values and commits to
its entire contents with a single root hash. All but the final byte
of a key are its **stem** and the final byte is the sub-index within
that stem, so keys sharing a stem form one group of values that open
together in one branch. Stems must be prefix-free; see [`Stem`].

Unlike the hexary Merkle Patricia Trie it is designed to replace, the
tree is strictly binary — each key is consumed bit by bit, most
significant bit first — and it uses no RLP and no extension nodes. A
path terminates in a [`StemNode`] as soon as the stem is unambiguous,
with [`InternalNode`]s only where stems share prefix bits. Stems are
hash outputs, so long shared prefixes are rare and extension nodes
would add node types and proof cases for little compression.

The mapping of keys to values is exposed through [`BinaryTrie`]; the
[`root`] function reduces a trie to its 32-byte commitment. The hash
function used here follows the EIP's reference implementation
(BLAKE3), but the EIP marks the final choice as open.

This module defines only the raw tree. How Ethereum state — accounts,
storage, and code — is mapped into keys and values is defined in
[`ethereum.binary_trie.embedding`].

[EIP-8297]: https://eips.ethereum.org/EIPS/eip-8297
[`Stem`]: ref:ethereum.binary_trie.trie.Stem
[`StemNode`]: ref:ethereum.binary_trie.trie.StemNode
[`InternalNode`]: ref:ethereum.binary_trie.trie.InternalNode
[`BinaryTrie`]: ref:ethereum.binary_trie.trie.BinaryTrie
[`root`]: ref:ethereum.binary_trie.trie.root
[`ethereum.binary_trie.embedding`]: ref:ethereum.binary_trie.embedding
"""

# TODO: For client testing, can we for now pretend that this will be
# activated at amsterdam, to see the spec changes needed to
# accommodate this.

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple, Union, final

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


Stem = Bytes
"""
Every byte of a [`Key`] except the last: the path walked through the
outer tree, bit by bit, most significant bit first, ending at a
[`StemNode`].

Stems must be **prefix-free**: no stem may be a prefix of another. A
key is its path, so a stem prefixing another would place a terminal
node on the interior of the longer stem's path, leaving the tree
ill-defined. Key assignment schemes guarantee the property by giving
every key of a category one fixed length. [`root`] asserts it while
splitting stems, where a violation surfaces as a stem running out of
bits before separating from its group.

[`Key`]: ref:ethereum.binary_trie.trie.Key
[`StemNode`]: ref:ethereum.binary_trie.trie.StemNode
[`root`]: ref:ethereum.binary_trie.trie.root
"""

Key = Bytes
"""
A full tree key: a [`Stem`] followed by one **sub-index** byte
selecting a value within the stem's group of 256.

The tree requires only that a key is at least two bytes, so a stem of
one or more bytes plus the sub-index, with stems prefix-free; the
key lengths actually in use are fixed by
[`ethereum.binary_trie.embedding`].

[`Stem`]: ref:ethereum.binary_trie.trie.Stem
[`ethereum.binary_trie.embedding`]: ref:ethereum.binary_trie.embedding
"""

StemValues = Tuple[
    Optional[Bytes32], ...  # 256 entries, one per sub-index
]
"""
Values held under a single stem, indexed by the key's final byte.

Always 256 entries long; `None` marks a sub-index with no value.
"""


@final
@slotted_freezable
@dataclass
class StemNode:
    """
    Node at the end of a stem's path, holding the group of up to 256
    values whose keys share that stem.
    """

    stem: Stem
    """
    Bytes shared by every key committed to by this node: each key of
    the group shares the same stem except its final byte, which
    indexes [`values`].

    [`values`]: ref:ethereum.binary_trie.trie.StemNode.values
    """

    values: StemValues
    """
    Values of this stem's keys, indexed by each key's final byte.

    Entry `i` is leaf `i`, left to right, of the fixed 256-leaf
    subtree committed to by [`merkleize`]; `None` marks an absent
    value, which commits to 32 zero bytes.

    [`merkleize`]: ref:ethereum.binary_trie.trie.merkleize
    """


@final
@slotted_freezable
@dataclass
class InternalNode:
    """
    Binary branch with a left subtree (bit `0`) and a right subtree
    (bit `1`).
    """

    left: Optional["BinaryNode"]
    """
    Subtree of stems whose next bit is `0`, or `None` if that subtree
    is empty.
    """

    right: Optional["BinaryNode"]
    """
    Subtree of stems whose next bit is `1`, or `None` if that subtree
    is empty.
    """


BinaryNode = Union[InternalNode, StemNode]
"""
Either of the node types making up a binary tree; an empty subtree is
represented by `None`.
"""


@final
@dataclass
class BinaryTrie:
    """
    Mapping of variable-length keys to 32-byte values with a single
    root hash that uniquely identifies its contents.

    Only the key/value pairs are stored; [`root`] rebuilds the node
    structure and rehashes it from scratch on every call. Clients are
    expected to keep nodes and update hashes incrementally instead.

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

    A key is a stem of one or more bytes followed by the sub-index
    byte. The caller must keep stems prefix-free; see [`Stem`].

    [`Stem`]: ref:ethereum.binary_trie.trie.Stem
    """
    assert len(key) >= 2
    assert len(value) == 32
    trie._data[key] = value


def trie_get(trie: BinaryTrie, key: Key) -> Optional[Bytes32]:
    """
    Look up `key` in `trie`, returning `None` if absent.
    """
    return trie._data.get(key)


def merkle_hash(data: Optional[Bytes]) -> Hash32:
    """
    Hash node data, mapping an absent value and the concatenation of
    two empty subtree hashes to 32 zero bytes.

    Inputs are a 32-byte leaf value, a 64-byte sibling pairing, or a
    stem node preimage, whose length varies with the stem.
    """
    if data is None or data == b"\x00" * 64:
        return EMPTY_TRIE_ROOT
    # Valid inputs are 32-byte leaves, 64-byte pairings, or stem
    # preimages of at least 34 bytes (a one-byte stem, the type byte,
    # and the 32-byte subtree root), so length 33 is impossible. This
    # 34 is unrelated to the embedding's ACCOUNT_KEY_LENGTH of 34 (a
    # full key: 33-byte stem plus sub-index); the numbers coinciding
    # is an accident, so do not unify them into one constant.
    assert len(data) == 32 or len(data) >= 34
    return blake3_hash(data)


def merkleize(node: Optional[BinaryNode]) -> Hash32:
    """
    Compute the hash committing to `node` and everything below it.
    """
    if node is None:
        return EMPTY_TRIE_ROOT
    if isinstance(node, InternalNode):
        left_hash = merkleize(node.left)
        right_hash = merkleize(node.right)
        return merkle_hash(left_hash + right_hash)

    assert len(node.values) == 256
    level: List[Hash32] = [merkle_hash(value) for value in node.values]
    while len(level) > 1:
        level = [
            merkle_hash(level[i] + level[i + 1])
            for i in range(0, len(level), 2)
        ]
    return merkle_hash(node.stem + b"\x00" + level[0])


def binarize(
    stems: Mapping[Stem, StemValues], depth: Uint
) -> Optional[BinaryNode]:
    """
    Recursively build the tree for `stems`, starting `depth` bits into
    the stems.
    """
    if len(stems) == 0:
        return None

    if len(stems) == 1:
        (stem,) = stems
        return StemNode(stem, stems[stem])

    left: Dict[Stem, StemValues] = {}
    right: Dict[Stem, StemValues] = {}
    for stem, values in stems.items():
        # A stem running out of bits while still grouped with another
        # stem means it is a prefix of that stem, which the key
        # assignment must never produce.
        assert depth < Uint(8) * Uint(len(stem))
        if bytes_to_bit_list(stem)[depth] == 0:
            left[stem] = values
        else:
            right[stem] = values
    return InternalNode(
        binarize(left, depth + Uint(1)),
        binarize(right, depth + Uint(1)),
    )


def root(trie: BinaryTrie) -> Hash32:
    """
    Compute the root hash of `trie`.
    """
    grouped: Dict[Stem, List[Optional[Bytes32]]] = {}
    for key, value in trie._data.items():
        stem = Stem(key[:-1])
        if stem not in grouped:
            grouped[stem] = [None] * 256
        grouped[stem][key[-1]] = value

    stems = {stem: tuple(values) for stem, values in grouped.items()}
    return merkleize(binarize(stems, Uint(0)))
