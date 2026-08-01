# Fork-Aware PBT Test Fixture Format Implementation Plan

**Status:** Implemented locally and left uncommitted for review.

**Goal:** Add a minimal `pbt_test` JSON fixture format that fills raw
Partitioned Binary Tree root vectors for a selected Ethereum fork without
executing an EVM state transition.

**Architecture:** PBT tests use ordinary fork selection and validity markers,
but generate their fixture directly from the EELS binary-trie implementation.
`BaseDirectTest` provides the reusable lifecycle for fork-aware tests that do
not need `t8n`, pre-allocation, gas accounting, or EVM execution. Direct tests
and existing execution tests share fixture-format parametrization, collection,
metadata, indexes, xdist handling, and output packaging. The filling and fork
plugins do not contain PBT-specific control-flow exceptions.

**Review constraint:** Do not commit. Leave all changes on
`kw/add-trie-testing-logic` for review.

## Task 1: Direct fixture-test lifecycle

**Files:**

- Create: `packages/testing/src/execution_testing/specs/base_direct.py`
- Modify: `packages/testing/src/execution_testing/specs/__init__.py`
- Modify: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/filler/filler.py`
- Modify: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/shared/execute_fill.py`
- Modify: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/shared/helpers.py`
- Modify: `packages/testing/src/execution_testing/fixtures/base.py`

### Design

`BaseDirectTest` is a Pydantic test-spec base with:

- a required concrete `Fork | TransitionFork` supplied by the filler;
- a registry of direct test types;
- declared `supported_fixture_formats`;
- the same fixture-format marker filtering contract as `BaseTest`;
- a `generate(fixture_format=...) -> BaseFixture` interface.

The filler dynamically creates one pytest fixture per registered direct test
type. Its wrapper injects the selected fork, generates and annotates the
fixture, and passes it to `FixtureCollector`. It deliberately supplies no
transition-tool version or metadata. Wrapper subclasses are excluded from the
test-spec registry.

`get_fill_test_types()` returns both existing `BaseTest` types and direct test
types. Shared fill validation, fixture-format parametrization, marker
registration, and item-to-format lookup use this function rather than naming
PBT explicitly.

`BaseFixture.fill_info()` accepts an optional transition-tool version. Existing
execution fixtures continue to provide one; direct fixtures omit the
`filling-transition-tool` metadata field.

## Task 2: PBT fixture and test-spec models

**Files:**

- Create: `packages/testing/src/execution_testing/fixtures/pbt.py`
- Create: `packages/testing/src/execution_testing/specs/pbt.py`
- Create: `packages/testing/src/execution_testing/specs/tests/test_pbt.py`
- Modify: `packages/testing/src/execution_testing/fixtures/__init__.py`
- Modify: `packages/testing/src/execution_testing/specs/__init__.py`
- Modify: `packages/testing/src/execution_testing/__init__.py`

### Fixture contract

The first supported operation is `tree_root`:

```python
class PBTEntry(CamelModel):
    key: Bytes
    value: Hash


class PBTTreeRootInput(CamelModel):
    entries: List[PBTEntry]


class PBTTreeRootExpected(CamelModel):
    root: Hash


class PBTFixture(BaseFixture):
    format_name = "pbt_test"
    fork: Fork | TransitionFork = Field(..., alias="network")
    operation: Literal["tree_root"]
    input: PBTTreeRootInput
    expected: PBTTreeRootExpected
```

The generated JSON is self-contained and identifies the fork:

```json
{
  "network": "BinaryTree",
  "operation": "tree_root",
  "input": {"entries": []},
  "expected": {
    "root": "0x0000000000000000000000000000000000000000000000000000000000000000"
  }
}
```

`PBTTest` derives from `BaseDirectTest`, supports `PBTFixture`, and exposes the
pytest parameter name `pbt_test`. Its generator inserts the declared entries
into `ethereum.binary_trie.trie.BinaryTrie` and computes the reference root.
`PBTTestFiller = Type[PBTTest]` and the fixture models are exported from the
public testing package.

### Model test

The unit test constructs `PBTTest(fork=BinaryTree, entries=[])`, generates a
`PBTFixture`, and verifies its operation, input, empty root, and fork.

Run from the repository root:

```console
uv run pytest -q packages/testing/src/execution_testing/specs/tests/test_pbt.py
```

## Task 3: Filler integration

**Files:**

- Create: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/filler/tests/test_pbt_filler.py`
- Modify: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/filler/filler.py`
- Modify: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/shared/execute_fill.py`
- Modify: `packages/testing/src/execution_testing/cli/pytest_commands/plugins/shared/helpers.py`

The pytester integration test creates a `PBTTestFiller` test marked
`valid_from("BinaryTree")` and fills it with `--fork=BinaryTree`. It verifies:

- one test passes;
- output is written beneath `pbt_tests/for_binarytree/`;
- the fixture contains `network`, `operation`, `input`, and `expected.root`;
- `_info.fixture-format` is `pbt_test`;
- `_info` does not claim a filling transition tool;
- the fixture index records `fork: BinaryTree` and `format: pbt_test`.

Run from the repository root:

```console
uv run pytest -q packages/testing/src/execution_testing/cli/pytest_commands/plugins/filler/tests/test_pbt_filler.py
```

## Task 4: Declarative PBT vectors and documentation

**Files:**

- Create: `tests/pbt/__init__.py`
- Create: `tests/pbt/test_tree_root.py`
- Modify: `docs/filling_tests/filling_tests_command_line.md`

The initial vector set contains:

- an empty tree;
- a single leaf;
- two leaves with a shared prefix.

The module is marked `valid_from("BinaryTree")`. Tests declare only their
entries; expected roots are filled from the reference implementation.

Fill sequentially from the repository root:

```console
uv run fill --fork=BinaryTree --output=fixtures/pbt-prototype --no-html tests/pbt
```

Fill with xdist:

```console
uv run fill --fork=BinaryTree --output=fixtures/pbt-prototype-xdist --no-html -n 2 tests/pbt
```

Both modes produce three tests beneath:

```text
pbt_tests/for_binarytree/pbt/tree_root/
```

The fixture index lists `BinaryTree` as its only fork and `pbt_test` as its
only format.

## Task 5: Verification

Completed verification from the repository root:

```console
uv run pytest -q packages/testing/src/execution_testing/specs/tests/test_pbt.py \
  packages/testing/src/execution_testing/cli/pytest_commands/plugins/filler/tests/test_pbt_filler.py \
  packages/testing/src/execution_testing/fixtures/tests/test_base.py \
  packages/testing/src/execution_testing/cli/pytest_commands/plugins/forks/tests/test_forks.py
```

Result: `12 passed`.

```console
uv run pytest -q tests/binary_trie/test_trie.py \
  tests/binary_trie/test_embedding.py \
  tests/binary_trie/test_state_pbt.py
```

Result: `121 passed`.

The affected regression suite, excluding its benchmark module, completed with
`392 passed, 6 skipped, 1 xfailed`. Sequential and two-worker PBT fills each
completed with three passing tests and a correct merged fixture index.

Ruff lint, Ruff formatting, focused mypy, and `git diff --check` pass.

A broader run also reached `418 passed, 6 skipped, 1 xfailed`, but 12 benchmark
harness tests failed before collection because their temporary test directories
did not contain the configured `evm` executable. All 12 failures reported the
same missing-binary error and were unrelated to the PBT/direct-test changes.

No commit has been created.
