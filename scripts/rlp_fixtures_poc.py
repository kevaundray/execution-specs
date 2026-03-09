"""
Benchmark JSON vs RLP fixture loading using the codec abstraction.

Usage:
    .venv/bin/python scripts/rlp_fixtures_poc.py [fixture.json ...]
"""

import sys
import timeit
from pathlib import Path
from typing import Any

from execution_testing.fixtures.codec import JsonCodec, RlpCodec


def benchmark_file(fixture_path: Path, iterations: int = 20) -> None:
    """Benchmark JSON vs RLP load speed for a single fixture file."""
    json_codec = JsonCodec()
    rlp_codec = RlpCodec()

    json_bytes = fixture_path.read_bytes()
    json_size = len(json_bytes)

    # Convert to RLP
    original = json_codec.load_fixtures(json_bytes)
    rlp_data = rlp_codec.dump_fixtures(original)
    rlp_size = len(rlp_data)

    # Verify round-trip
    assert rlp_codec.load_fixtures(rlp_data) == original, "Round-trip failed!"

    def load_json() -> Any:
        return json_codec.load_fixtures(json_bytes)

    def load_rlp() -> Any:
        return rlp_codec.load_fixtures(rlp_data)

    # Warmup
    load_json()
    load_rlp()

    json_time = timeit.timeit(load_json, number=iterations) / iterations
    rlp_time = timeit.timeit(load_rlp, number=iterations) / iterations

    name = fixture_path.stem
    print(f"\n{name} ({json_size:,} bytes JSON / {rlp_size:,} bytes RLP)")
    print(f"  JSON parse:  {json_time * 1000:8.2f} ms")
    print(f"  RLP decode:  {rlp_time * 1000:8.2f} ms")
    print(f"  Speedup:     {json_time / rlp_time:.2f}x")
    print(f"  Size ratio:  {rlp_size / json_size:.1%}")


def main() -> None:
    fixtures_base = Path(
        "tests/json_infra/fixtures/latest_fork_tests/fixtures/state_tests"
    )
    test_files = [
        fixtures_base / "frontier/opcodes/test_calldatasize.json",
        fixtures_base
        / "cancun/eip4844_blobs/test_insufficient_balance_blob_tx.json",
        fixtures_base
        / "byzantium/eip198_modexp_precompile/test_modexp.json",
        fixtures_base
        / "osaka/eip7883_modexp_gas_increase/test_modexp_boundary_inputs.json",
    ]

    if len(sys.argv) > 1:
        test_files = [Path(p) for p in sys.argv[1:]]

    for path in test_files:
        if not path.exists():
            print(f"Skipping (not found): {path}")
            continue
        benchmark_file(path)


if __name__ == "__main__":
    main()
