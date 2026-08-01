"""Integration tests for fork-aware PBT fixture filling."""

import json
import textwrap

import pytest


def test_pbt_test_fills_without_transition_tool(
    pytester: pytest.Pytester,
) -> None:
    """A PBT vector uses fork parametrization without transition execution."""
    tests_dir = pytester.mkdir("tests")
    test_file = tests_dir / "test_pbt.py"
    test_file.write_text(
        textwrap.dedent(
            """\
            import pytest

            from execution_testing import PBTTestFiller


            @pytest.mark.valid_from("BinaryTree")
            def test_empty_tree(pbt_test: PBTTestFiller) -> None:
                \"\"\"Fill the empty PBT root.\"\"\"
                pbt_test(entries=[])
            """
        )
    )

    pytester.copy_example(
        name=(
            "src/execution_testing/cli/pytest_commands/"
            "pytest_ini_files/pytest-fill.ini"
        )
    )
    output_dir = pytester.path / "fixtures"

    result = pytester.runpytest(
        "-c",
        "pytest-fill.ini",
        "--no-html",
        "--fork=BinaryTree",
        f"--output={output_dir}",
        str(test_file),
        "-q",
    )

    result.assert_outcomes(passed=1)
    fixture_paths = list((output_dir / "pbt_tests").rglob("*.json"))
    assert len(fixture_paths) == 1

    fixture_file = json.loads(fixture_paths[0].read_text())
    fixture = next(iter(fixture_file.values()))
    assert fixture["operation"] == "tree_root"
    assert fixture["network"] == "BinaryTree"
    assert fixture["input"] == {"entries": []}
    assert fixture["expected"]["root"] == "0x" + "00" * 32
    assert fixture["_info"]["fixture-format"] == "pbt_test"
    assert "filling-transition-tool" not in fixture["_info"]

    index = json.loads((output_dir / ".meta" / "index.json").read_text())
    assert index["test_count"] == 1
    assert len(index["test_cases"]) == 1
    assert index["test_cases"][0]["fork"] == "BinaryTree"
    assert index["test_cases"][0]["format"] == "pbt_test"
