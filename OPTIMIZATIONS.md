# Performance Optimizations

Optimizations for `tox -e py3` execution time, inspired by
[The Optimization Ladder](https://cemrehancavdar.com/2026/03/10/optimization-ladder/).

## Summary

| Change | Measurement | Improvement |
| ------ | ----------- | ----------- |
| EVM interpreter patches | Microbenchmark (50 × 20K ops) | 4.20s → 0.78s (**5.37x**) |
| Test infrastructure patches | 2055-test subset, `-n0` | 44.18s → 38.78s (**12%**) |
| slipcover replaces coverage.py | 2055-test subset, `-n0` | 105s → 39s (**2.69x**) |
| Python 3.14 upgrade | 2055-test subset, `-n0` | 44.18s → 38.81s (**12%**) |
| **All combined (3.14 + patches + slipcover)** | **2055-test subset, `-n0`** | **105s → ~35s (3x)** |
| All EVM + compiled loop + codecopy | Benchmark tests (20 codecopy) | 319s → 64s (**5.0x**) |

Projected CI impact: **~20 min → ~7 min** (full 98K suite with coverage).

## Architecture

All optimizations live outside the spec code in the `ethereum_optimized`
package. The spec code in `src/ethereum/forks/` is untouched.

```
src/ethereum_optimized/
├── __init__.py          # Entry points: monkey_patch_interpreter(), monkey_patch()
├── interpreter.py       # Optimized EVM functions (pure Python, no external deps)
├── evm_loop.py          # mypyc-compilable interpreter inner loop
├── evm_utils.py         # mypyc-compilable gas/memory utility functions
├── evm_u256.py          # mypyc-compilable U256 arithmetic primitives
├── state_db.py          # (existing) LMDB-backed state DB (requires rust_pyspec_glue)
├── fork.py              # (existing) Optimized ethash (requires ethash C library)
└── utils.py             # (existing) Shared helpers

scripts/
├── fill_wrapper.py      # slipcover-compatible fill entry point
└── slipcover_report.py  # Coverage XML generation + fail-under gate

tests/
└── conftest.py          # Calls monkey_patch_interpreter() + test infra caching
```

### How monkey patching works

The spec uses a "WET" (Write Everything Twice) architecture where each
fork is a complete copy. This means there are 24 forks × ~15 modules =
~360 modules with identical hot-path functions. We can't edit them all,
and we shouldn't (readability is the priority for spec code).

Instead, `monkey_patch_interpreter()` in `ethereum_optimized/__init__.py`
iterates all discovered forks and replaces functions at runtime:

```python
# Simplified — the real version handles all forks in a loop
import ethereum.forks.amsterdam.vm.runtime as runtime_mod
runtime_mod.get_valid_jump_destinations = fast_version

import ethereum.forks.amsterdam.vm.gas as gas_mod
gas_mod.charge_gas = fast_version
```

**The import-binding problem:** Python's `from X import Y` copies the
reference. If module A does `from gas import charge_gas`, patching
`gas.charge_gas` doesn't affect A's copy. We solve this by also patching
every instruction submodule that imported the function by name:

```python
for submod in ["arithmetic", "stack", "environment", ...]:
    mod = import_module(f"ethereum.forks.{fork}.vm.instructions.{submod}")
    if hasattr(mod, "charge_gas"):
        mod.charge_gas = fast_version  # Patch the copied binding too
```

The `op_implementation` dispatch dict is also updated so the interpreter
loop calls the optimized versions:

```python
op_impl = instructions_mod.op_implementation
op_impl[Ops.PUSH1] = optimized_push1
op_impl[Ops.CODECOPY] = optimized_codecopy
# etc.
```

**Activation:** `tests/conftest.py` calls `monkey_patch_interpreter()`
at import time, so all tests automatically get the optimized versions.
The existing `monkey_patch(state_path)` for the C-extension state DB
now also calls `monkey_patch_interpreter()`, so sync/json_loader users
get the optimizations too.

**mypyc modules:** `evm_loop.py`, `evm_utils.py`, and `evm_u256.py`
are plain Python that can optionally be compiled with mypyc for extra
speed. If the `.so` files are present, the compiled versions are used
automatically; if not, the Python source is used as a fallback. Compile
with:

```shell
mypyc src/ethereum_optimized/evm_loop.py
mypyc src/ethereum_optimized/evm_utils.py
mypyc src/ethereum_optimized/evm_u256.py
```

**Note on mypyc vs rewriting:** mypyc on unmodified spec code gives
zero speedup (actually slightly slower) because the bottleneck is
`Uint`/`U256` object construction from the `ethereum_types` package,
which mypyc can't optimize (it's an external type). The speedups come
from rewriting functions to use native `int` internally, and mypyc
makes those `int` operations slightly faster.

## Methodology

Bottlenecks were identified using `cProfile` on:

1. A synthetic EVM workload (PUSH1, PUSH1, ADD, POP × 5000, 50 iterations)
2. The full `fill` pipeline on a 512-test subset

Each optimization was measured independently before combining.

---

## EVM Interpreter Optimizations (2.58x)

### 1. `get_valid_jump_destinations` (27% of EVM time → ~0%)

**Problem:** The spec version iterates every byte of the contract code
using `Uint` arithmetic and `Ops` enum construction:

```python
# Spec version (slow)
pc = Uint(0)
while pc < ulen(code):
    current_opcode = Ops(code[pc])       # Enum construction per byte
    if current_opcode == Ops.JUMPDEST:
        valid_jump_destinations.add(pc)
    elif Ops.PUSH1.value <= current_opcode.value <= Ops.PUSH32.value:
        pc += Uint(push_data_size)       # Uint construction per PUSH
    pc += Uint(1)                        # Uint construction per byte
```

**Fix:** Use plain `int` internally. Only wrap in `Uint` when adding to
the set (JUMPDEST is rare relative to code length):

```python
# Optimized version
pc = 0
while pc < len(code):
    byte = code[pc]
    if byte == 0x5B:              # Plain int comparison
        add(Uint(pc))             # Uint only for rare JUMPDEST
    elif 0x60 <= byte <= 0x7F:
        pc += byte - 0x60 + 1    # Plain int arithmetic
    pc += 1
```

**Why it matters:** For a 6000-byte contract, the spec version creates
~18000 `Uint` objects and ~6000 `Ops` enum instances, all discarded.

**Impact:** 0.188s → 0.002s (per profiling run)

### 2. `evm_trace` short-circuit (3% of EVM time)

**Problem:** `evm_trace` is called 3 times per opcode (OpStart, OpEnd,
and inside `charge_gas` via GasAndRefund). When tracing is disabled
(the case during `tox -e py3`), each call still invokes
`discard_evm_trace`.

**Fix:** Check the active tracer identity before delegating:

```python
def evm_trace(evm, event):
    if _trace_mod._evm_trace is not discard:
        _trace_mod._evm_trace(evm, event)
```

**Impact:** 0.023s → 0.007s

### 3. `charge_gas` trace skip (5% of EVM time)

**Problem:** `charge_gas` unconditionally calls `evm_trace` with a
`GasAndRefund` dataclass even when tracing is disabled. Since
instruction modules import `charge_gas` by name, patching the gas
module alone isn't enough — every instruction submodule's binding must
be updated.

**Fix:** Skip trace when discard tracer is active. Patch both the gas
module and every instruction submodule that imported `charge_gas`.

**Impact:** 0.080s → 0.049s cumulative

### 4. `Uint` fast arithmetic (16% of EVM time)

**Problem:** `Uint.__iadd__` (used by `evm.pc += Uint(1)` in every
opcode) calls `Uint(result)` which goes through `__init__` →
`int(value)` → `_in_range(value)`. Since `Uint` is unbounded
non-negative, the `_in_range` check is redundant for addition.

**Fix:** Bypass `__init__` using `object.__new__` and set `_number`
directly:

```python
def _fast_iadd(self, right):
    obj = object.__new__(type(self))
    obj._number = self._number + right._number
    return obj
```

Also patched `__add__`, `__sub__`, `__isub__`, and `__init__` (fast
path for `int` args). Only applied to `Uint`, not `U256`/`U64` (those
have upper bounds that arithmetic could violate).

**Impact:** `Uint.__init__` calls dropped from 530K → 70K (87% fewer)

### 5. `push_n` / `dup_n` / `swap_n` / `pop` (6.5% of EVM time)

**Problem:** `push_n` (PUSH1-PUSH32) creates ~8 intermediate
`Uint`/`U256` objects through `buffer_read` → `right_pad_zero_bytes` →
`U256.from_be_bytes`:

```python
# Spec version: 8+ object constructions for reading 1 byte
data_to_push = U256.from_be_bytes(
    buffer_read(evm.code, U256(evm.pc + Uint(1)), U256(num_bytes))
)
evm.pc += Uint(1) + Uint(num_bytes)
```

**Fix:** Read code bytes directly with plain `int` indexing, construct
a single `U256` result, and use pre-computed `Uint` increments:

```python
_pc_increments = [Uint(n + 1) for n in range(33)]  # Pre-computed

def push_n(evm, num_bytes):
    charge_gas(evm, gas_very_low)
    pc = evm.pc._number              # Raw int access
    start = pc + 1
    end = start + num_bytes
    if end <= len(evm.code):
        data = int.from_bytes(evm.code[start:end], "big")
    else:
        ...  # Zero-padding for code past end
    stack_push(evm.stack, U256(data))
    evm.pc += _pc_increments[num_bytes]  # Pre-computed Uint
```

Similarly optimized `dup_n`, `swap_n`, and `pop` with a pre-cached
`Uint(1)`.

**Impact:** 0.046s → 0.021s own time; total benchmark 0.433s → 0.275s

### 5b. Inline `charge_gas`, `U256.__init__`, `U256.__eq__`, `type() is` checks

**Problem:** Profiling benchmark tests (EVM-heavy) revealed:

- `isinstance` — 14.7s for 206M calls. Every Uint/U256 arithmetic
  method does `isinstance(right, cls)` which traverses the MRO.
- `U256.__init__` — 8.5s for 30M calls. Only Uint was patched; U256
  still used the original `__init__` with `_in_range` method call.
- `U256.__eq__` — 5.2s for 21M calls. Checks `isinstance(other,
  SupportsInt)` via the typing module (6.6s in `typing.__instancecheck__`).
- `charge_gas` — 14.1s for 40M calls. Still calls `Uint.__lt__` and
  `Uint.__isub__` which each do isinstance + object creation.

**Fixes:**

1. Replace `isinstance(right, cls)` with `type(right) is not cls` in
   all fast arithmetic. Single pointer comparison vs MRO traversal.

2. Patch `U256.__init__` with a fast path for `int` args (same pattern
   as Uint but with upper bound check).

3. Patch `U256.__eq__` to fast-path `U256 == U256` and `U256 == int`
   without going through `isinstance(other, SupportsInt)`.

4. Inline `charge_gas` to access `gas_left._number` directly, bypassing
   `Uint.__lt__` and `Uint.__isub__` entirely.

**Impact:** Benchmark tests 140s → 125s (11% faster). EVM microbenchmark
0.90s → 0.78s (16% faster, total 5.37x vs baseline).

### EVM cumulative impact

| Stage | Profiled time | Function calls | vs Baseline |
| ----- | ------------- | -------------- | ----------- |
| Baseline | 0.694s | 3.4M | -- |
| + jump destinations | 0.491s | 2.2M | 1.41x |
| + trace short-circuit | 0.457s | 2.2M | 1.52x |
| + Uint arithmetic | 0.433s | 1.9M | 1.60x |
| + push_n/dup/swap/pop | 0.275s | 1.2M | 2.52x |
| + compiled loop | 0.183s | -- | 3.79x |
| + inline charge_gas, U256 init/eq | 0.156s | -- | 4.45x |

---

## Test Infrastructure Optimizations (~12% wall time)

### 6. GC disable + fork helper caching

**Problem:** Profiling the full fill pipeline revealed the top
bottlenecks are test infrastructure, not EVM execution:

- `gc.collect`: 4.5s (5 calls) -- garbage collection
- `pathlib` operations: ~14s (5M+ Path constructions)
- `transition_fork_to`: 1.7s (684K calls) -- uncached fork lookups
- `BaseForkMeta._is_subclass_of`: 0.7s (2.4M calls) -- uncached
- pytest marker iteration: 2.0s (6M calls)

**Fixes applied:**

1. **Disable GC** during the fill run (`gc.disable()`). Re-enabled at
   exit via `atexit`. The fill process is short-lived so deferred
   collection is safe.

2. **Cache `transition_fork_to`** with `lru_cache`. This function
   iterates all transition forks on every call but depends only on
   `fork_to`. With 24 forks, nearly every call after warmup is a hit.

3. **Cache `_is_subclass_of`** on `BaseForkMeta`. Fork subclass
   relationships are static after import. The 2.4M calls reduce to ~50
   unique (fork, fork) pairs.

**Impact:** 44.18s → 38.78s on a 2055-test subset (12% faster).

---

## Coverage Tool: slipcover replaces coverage.py (2.69x)

### 7. slipcover integration

**Problem:** `coverage.py` with branch coverage adds 2.69x overhead to
`tox -e py3`. Even with `sys.monitoring` (PEP 669) on Python 3.12+,
coverage.py traces 194 files across 25 forks. This is the single
biggest contributor to CI wall time (~20 min with coverage vs ~7 min
without).

**Fix:** Replace `coverage.py` with [slipcover](https://github.com/plasma-umass/slipcover),
which uses bytecode rewriting instead of tracing. slipcover adds
essentially **zero overhead** while still providing line + branch
coverage.

| Method | Time (2055 tests) | Overhead vs no-cov |
| ------ | ----------------- | ------------------ |
| No coverage | 39.00s | -- |
| slipcover (branch) | 39.14s | ~0% |
| coverage.py (branch) | 104.99s | 2.69x |

**Integration:** The `tox -e py3` command now runs fill via
`python -m slipcover` wrapping `scripts/fill_wrapper.py`, with a
post-processing step (`scripts/slipcover_report.py`) that generates
Cobertura XML and enforces the 85% coverage gate.

The `fill_wrapper.py` exists because slipcover's `-m` flag conflicts
with pytest's `-m` (marker expression) -- the wrapper injects
`-m "not slow and not benchmark"` before passing args to fill.

---

## Python Version Upgrade (Rung 0)

### 8. Python 3.12 → 3.14

The blog post's "Rung 0" -- just upgrading the runtime:

| | Python 3.12 | Python 3.14 | Speedup |
| - | ----------- | ----------- | ------- |
| No patches | 44.18s | 38.81s | 12.2% |
| All patches | 38.78s | 34.54s | 10.9% |

3.14 improvements come from faster `pathlib` (rewritten in 3.13),
better adaptive specialization, and faster `isinstance`/`issubclass`.

---

## JSON: orjson drop-in

### 9. orjson with stdlib fallback

The blog post highlighted `json.loads()` as a bottleneck. The EELS T8N
bridge does a redundant JSON roundtrip (Python dict → JSON string →
Python dict) within the same process. We added orjson-with-fallback in
the two hot-path files:

- `execution_specs.py` (serialization side)
- `t8n/__init__.py` (deserialization side)

**Measured impact: negligible** (~0.1s) because the t8n cache is 100%
hit rate -- most tests skip the JSON path entirely. The bigger win
would be eliminating the roundtrip, but that requires modifying the T8N
tool interface.

---

## Future Work

### Remaining infrastructure bottlenecks

- `pathlib` (~14s) -- 5M+ Path constructions from pytest. Would improve
  with Python 3.13+ (pathlib rewrite) or reducing collected items.
- pytest markers/fixtures (~6s) -- internal pytest overhead.
- Eliminating the T8N JSON roundtrip (~3.9s) -- pass Python dicts
  directly instead of serializing through StringIO.

### Optimizing `ethereum_types`

The fundamental bottleneck is the `Uint`/`U256` design in the
`ethereum_types` package: every arithmetic operation creates a new
Python object with validation. Our monkey patches work around this
from the outside, but the cleanest long-term fix would be to optimize
the package itself (e.g., use `__slots__`, skip redundant `_in_range`
checks in known-safe paths, or rewrite in Rust/C).
