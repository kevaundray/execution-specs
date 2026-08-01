"""Raw Partitioned Binary Tree root vectors."""

import pytest
from execution_testing import PBTEntry, PBTTestFiller

pytestmark = pytest.mark.valid_from("BinaryTree")


def test_empty_tree(pbt_test: PBTTestFiller) -> None:
    """Fill the root of an empty tree."""
    pbt_test(entries=[])


def test_single_leaf(pbt_test: PBTTestFiller) -> None:
    """Fill the root of a tree containing one leaf."""
    pbt_test(
        entries=[
            PBTEntry(
                key=b"\x00" * 32,
                value=b"\x11" * 32,
            )
        ]
    )


def test_two_leaves_with_shared_prefix(pbt_test: PBTTestFiller) -> None:
    """Fill two leaves whose keys diverge at their final bit."""
    pbt_test(
        entries=[
            PBTEntry(
                key=b"\x00" * 32,
                value=b"\x11" * 32,
            ),
            PBTEntry(
                key=b"\x00" * 31 + b"\x01",
                value=b"\x22" * 32,
            ),
        ]
    )
