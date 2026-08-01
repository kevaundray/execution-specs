"""Tests for fork-aware PBT fixture generation."""

from execution_testing import PBTTest
from execution_testing.fixtures import PBTFixture
from execution_testing.forks import BinaryTree


def test_empty_tree_generates_fork_aware_root_fixture() -> None:
    """An empty entry set fills the EIP-8297 empty-tree root."""
    fixture = PBTTest(fork=BinaryTree, entries=[]).generate(
        fixture_format=PBTFixture
    )

    assert isinstance(fixture, PBTFixture)
    assert fixture.operation == "tree_root"
    assert fixture.input.entries == []
    assert fixture.expected.root == b"\x00" * 32
    assert fixture.get_fork() is BinaryTree
