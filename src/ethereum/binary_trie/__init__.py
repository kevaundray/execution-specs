"""
Partitioned Binary Tree, the binary state tree proposed by [EIP-8297].

The raw key/value tree — nodes, merkleization, and root computation —
is defined in [`ethereum.binary_trie.trie`]. How Ethereum state
(accounts, storage, and code) is mapped into the tree's keys and
values is defined in [`ethereum.binary_trie.embedding`].

[EIP-8297]: https://eips.ethereum.org/EIPS/eip-8297
[`ethereum.binary_trie.trie`]: ref:ethereum.binary_trie.trie
[`ethereum.binary_trie.embedding`]: ref:ethereum.binary_trie.embedding
"""
