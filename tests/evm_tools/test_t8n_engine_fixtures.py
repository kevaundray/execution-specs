"""
Tests for T8N engine mode against execution-spec-tests fixtures.

These tests verify that our Payload parser correctly handles fixture data
and that block hash validation works. Full state transition testing
requires Hive.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U64

# Path to fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "latest_fork_tests" / "fixtures" / "blockchain_tests_engine"


def discover_fixture_files(fork: str, limit: int = 10) -> List[Path]:
    """Discover fixture files for a given fork."""
    fork_dir = FIXTURES_DIR / fork
    if not fork_dir.exists():
        return []

    files = []
    for root, _, filenames in os.walk(fork_dir):
        for filename in filenames:
            if filename.endswith(".json"):
                files.append(Path(root) / filename)
                if len(files) >= limit:
                    return files
    return files


def load_fixture_payloads(fixture_path: Path) -> List[Tuple[str, str, Dict[str, Any], List[Any]]]:
    """
    Load payloads from a fixture file.

    Returns list of (test_name, network, payload_params, extra_params) tuples.
    """
    with open(fixture_path) as f:
        data = json.load(f)

    results = []
    for test_name, test_data in data.items():
        network = test_data.get("network", "Unknown")
        for payload in test_data.get("engineNewPayloads", []):
            params = payload.get("params", [])
            if params:
                payload_data = params[0]
                extra_params = params[1:] if len(params) > 1 else []
                results.append((test_name, network, payload_data, extra_params))

    return results


# Check if fixtures exist
FIXTURES_EXIST = FIXTURES_DIR.exists() and any(FIXTURES_DIR.iterdir())

skip_no_fixtures = pytest.mark.skipif(
    not FIXTURES_EXIST,
    reason="Fixtures not downloaded - run pytest to auto-download"
)


@skip_no_fixtures
class TestPayloadParsingFromFixtures:
    """Test that our Payload class can parse fixture data."""

    @pytest.mark.evm_tools
    def test_parse_cancun_fixture_payload(self) -> None:
        """Test parsing Cancun payload data from fixtures."""
        from ethereum.forks.cancun.fork_types import Address, Bloom
        from ethereum.utils.hexadecimal import hex_to_bytes
        from ethereum_types.numeric import U256, Uint

        fixture_files = discover_fixture_files("cancun", limit=1)
        if not fixture_files:
            pytest.skip("No Cancun fixtures found")

        payloads = load_fixture_payloads(fixture_files[0])
        cancun_payloads = [(t, n, p, e) for t, n, p, e in payloads if n == "Cancun"]

        if not cancun_payloads:
            pytest.skip("No Cancun payloads in fixture")

        test_name, network, payload_data, extra_params = cancun_payloads[0]

        # Verify we can parse all required fields
        assert "parentHash" in payload_data
        assert "feeRecipient" in payload_data
        assert "stateRoot" in payload_data
        assert "receiptsRoot" in payload_data
        assert "logsBloom" in payload_data
        assert "prevRandao" in payload_data
        assert "blockNumber" in payload_data
        assert "gasLimit" in payload_data
        assert "gasUsed" in payload_data
        assert "timestamp" in payload_data
        assert "extraData" in payload_data
        assert "baseFeePerGas" in payload_data
        assert "blockHash" in payload_data
        assert "transactions" in payload_data

        # Cancun-specific
        assert "blobGasUsed" in payload_data
        assert "excessBlobGas" in payload_data
        assert "withdrawals" in payload_data

        # V3 extra params
        assert len(extra_params) >= 2, "V3 should have blob hashes and parent beacon root"

        # Parse to verify format is correct
        parent_hash = hex_to_bytes(payload_data["parentHash"])
        assert len(parent_hash) == 32

        block_hash = hex_to_bytes(payload_data["blockHash"])
        assert len(block_hash) == 32

        fee_recipient = hex_to_bytes(payload_data["feeRecipient"])
        assert len(fee_recipient) == 20

        print(f"\nParsed payload from: {test_name}")
        print(f"Block number: {int(payload_data['blockNumber'], 16)}")
        print(f"Transactions: {len(payload_data['transactions'])}")
        print(f"Blob hashes: {len(extra_params[0]) if extra_params else 0}")

    @pytest.mark.evm_tools
    def test_parse_shanghai_fixture_payload(self) -> None:
        """Test parsing Shanghai payload data from fixtures."""
        fixture_files = discover_fixture_files("shanghai", limit=1)
        if not fixture_files:
            pytest.skip("No Shanghai fixtures found")

        payloads = load_fixture_payloads(fixture_files[0])
        shanghai_payloads = [(t, n, p, e) for t, n, p, e in payloads if n == "Shanghai"]

        if not shanghai_payloads:
            pytest.skip("No Shanghai payloads in fixture")

        test_name, network, payload_data, extra_params = shanghai_payloads[0]

        # Verify Shanghai-specific field
        assert "withdrawals" in payload_data

        print(f"\nParsed Shanghai payload from: {test_name}")
        print(f"Withdrawals: {len(payload_data.get('withdrawals', []))}")


@skip_no_fixtures
class TestBlockHashValidation:
    """Test block hash validation from fixtures."""

    @pytest.mark.evm_tools
    def test_cancun_block_hash_computation(self) -> None:
        """Test that we can compute the correct block hash for Cancun payloads."""
        from ethereum_rlp import rlp

        from ethereum.crypto.hash import keccak256
        from ethereum.forks.cancun.engine import ExecutionPayloadV3, payload_to_header
        from ethereum.forks.cancun.fork_types import Address, Bloom
        from ethereum.utils.hexadecimal import hex_to_bytes
        from ethereum_types.numeric import U256, Uint

        fixture_files = discover_fixture_files("cancun", limit=1)
        if not fixture_files:
            pytest.skip("No Cancun fixtures found")

        payloads = load_fixture_payloads(fixture_files[0])
        cancun_payloads = [(t, n, p, e) for t, n, p, e in payloads if n == "Cancun"]

        if not cancun_payloads:
            pytest.skip("No Cancun payloads in fixture")

        validated_count = 0
        for test_name, network, payload_data, extra_params in cancun_payloads[:5]:
            # Parse withdrawals
            withdrawals_data = payload_data.get("withdrawals", [])
            from ethereum.forks.cancun.blocks import Withdrawal
            withdrawals = tuple(
                Withdrawal(
                    index=Uint(int(w["index"], 16)),
                    validator_index=Uint(int(w["validatorIndex"], 16)),
                    address=Address(hex_to_bytes(w["address"])),
                    amount=Uint(int(w["amount"], 16)),
                )
                for w in withdrawals_data
            )

            # Build ExecutionPayloadV3
            execution_payload = ExecutionPayloadV3(
                parent_hash=hex_to_bytes(payload_data["parentHash"]),
                fee_recipient=Address(hex_to_bytes(payload_data["feeRecipient"])),
                state_root=hex_to_bytes(payload_data["stateRoot"]),
                receipts_root=hex_to_bytes(payload_data["receiptsRoot"]),
                logs_bloom=Bloom(hex_to_bytes(payload_data["logsBloom"])),
                prev_randao=Bytes32(hex_to_bytes(payload_data["prevRandao"])),
                block_number=Uint(int(payload_data["blockNumber"], 16)),
                gas_limit=Uint(int(payload_data["gasLimit"], 16)),
                gas_used=Uint(int(payload_data["gasUsed"], 16)),
                timestamp=U256(int(payload_data["timestamp"], 16)),
                extra_data=hex_to_bytes(payload_data["extraData"]),
                base_fee_per_gas=Uint(int(payload_data["baseFeePerGas"], 16)),
                block_hash=hex_to_bytes(payload_data["blockHash"]),
                transactions=tuple(
                    hex_to_bytes(tx) for tx in payload_data.get("transactions", [])
                ),
                withdrawals=withdrawals,
                blob_gas_used=U64(int(payload_data.get("blobGasUsed", "0x0"), 16)),
                excess_blob_gas=U64(int(payload_data.get("excessBlobGas", "0x0"), 16)),
            )

            # Get parent beacon block root from extra params
            parent_beacon_root = Bytes32(bytes(32))
            if extra_params and len(extra_params) > 1 and extra_params[1]:
                parent_beacon_root = Bytes32(hex_to_bytes(extra_params[1]))

            # Compute expected hash
            header = payload_to_header(execution_payload, parent_beacon_root)
            computed_hash = keccak256(rlp.encode(header))

            # Compare with fixture hash
            expected_hash = hex_to_bytes(payload_data["blockHash"])
            assert computed_hash == expected_hash, (
                f"Block hash mismatch for {test_name}: "
                f"computed {computed_hash.hex()}, expected {expected_hash.hex()}"
            )
            validated_count += 1

        assert validated_count > 0
        print(f"\nValidated {validated_count} Cancun block hashes")


@skip_no_fixtures
class TestT8NPayloadParsing:
    """Test T8N Payload class can parse fixture-style data."""

    @pytest.mark.evm_tools
    def test_t8n_payload_parses_cancun_fixture(self) -> None:
        """Test that T8N Payload class can parse Cancun fixture data."""
        import sys
        import tempfile

        from ethereum_spec_tools.evm_tools import create_parser
        from ethereum_spec_tools.evm_tools.t8n import ForkCache, T8N

        fixture_files = discover_fixture_files("cancun", limit=1)
        if not fixture_files:
            pytest.skip("No Cancun fixtures found")

        payloads = load_fixture_payloads(fixture_files[0])
        cancun_payloads = [(t, n, p, e) for t, n, p, e in payloads if n == "Cancun"]

        if not cancun_payloads:
            pytest.skip("No Cancun payloads in fixture")

        test_name, network, payload_data, extra_params = cancun_payloads[0]

        # Add V3 request params to payload
        t8n_payload = dict(payload_data)
        if extra_params and len(extra_params) > 0:
            t8n_payload["blobVersionedHashes"] = extra_params[0]
        if extra_params and len(extra_params) > 1:
            t8n_payload["parentBeaconBlockRoot"] = extra_params[1]

        with tempfile.TemporaryDirectory() as tmpdir:
            payload_path = os.path.join(tmpdir, "payload.json")
            with open(payload_path, "w") as f:
                json.dump(t8n_payload, f)

            alloc_path = os.path.join(tmpdir, "alloc.json")
            with open(alloc_path, "w") as f:
                json.dump({}, f)

            parser = create_parser()
            args = parser.parse_args([
                "t8n",
                "--state.fork", "Cancun",
                "--input.alloc", alloc_path,
                "--input.payload", payload_path,
                "--output.basedir", tmpdir,
            ])

            with ForkCache() as cache:
                t8n = T8N(args, sys.stdout, sys.stdin, cache)

                assert t8n.engine_mode is True
                assert t8n.payload is not None

                # Verify parsed fields
                assert int(t8n.payload.block_number) > 0
                assert t8n.payload.blob_gas_used is not None
                assert t8n.payload.excess_blob_gas is not None

                # Verify V3 request fields were parsed
                if extra_params:
                    assert t8n.payload.blob_versioned_hashes is not None
                    assert len(t8n.payload.blob_versioned_hashes) == len(extra_params[0])

                print(f"\nT8N parsed Cancun payload successfully")
                print(f"Block number: {t8n.payload.block_number}")
                print(f"Transactions: {len(t8n.payload.transactions)}")
                print(f"Blob hashes: {len(t8n.payload.blob_versioned_hashes or [])}")
