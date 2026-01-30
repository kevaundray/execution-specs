"""
Parse blockchain_tests_engine fixture format for T8N engine validation.
"""
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.numeric import U64, U256, Uint

from ethereum.crypto.hash import Hash32
from ethereum.utils.hexadecimal import hex_to_bytes, hex_to_u64, hex_to_u256, hex_to_uint

if TYPE_CHECKING:
    from ethereum_spec_tools.evm_tools.t8n import T8N


class EngineFixture:
    """
    Parses blockchain_tests_engine fixture format.

    Fixture structure:
    {
        "test_name": {
            "network": "Cancun",
            "genesisBlockHeader": {...},
            "pre": {...},
            "postState": {...},
            "lastblockhash": "0x...",
            "engineNewPayloads": [...]
        }
    }
    """

    network: str
    genesis_header: Dict[str, Any]
    pre_state: Dict[str, Any]
    post_state: Dict[str, Any]
    last_block_hash: Hash32
    engine_payloads: List[Dict[str, Any]]
    test_name: str

    def __init__(self, t8n: "T8N", stdin: Optional[Dict] = None) -> None:
        """
        Initialize EngineFixture by parsing fixture JSON.

        Parameters
        ----------
        t8n :
            The T8N instance containing options.
        stdin :
            Optional dict containing stdin data.
        """
        if t8n.options.input_fixture == "stdin":
            assert stdin is not None
            data = stdin.get("fixture", stdin)
        else:
            with open(t8n.options.input_fixture, "r") as f:
                data = json.load(f)

        # Fixture file contains {test_name: test_data}
        # Get the first (usually only) test
        self.test_name = list(data.keys())[0]
        test_data = data[self.test_name]

        self.network = test_data["network"]
        self.genesis_header = test_data["genesisBlockHeader"]
        self.pre_state = test_data["pre"]
        self.post_state = test_data.get("postState", {})
        self.last_block_hash = Hash32(hex_to_bytes(test_data["lastblockhash"]))
        self.engine_payloads = test_data["engineNewPayloads"]

    def get_parent_header(self, fork: Any) -> Any:
        """
        Create a Header object from genesisBlockHeader.

        Parameters
        ----------
        fork :
            The ForkLoad instance for accessing fork-specific classes.

        Returns
        -------
        header :
            Fork-specific Header object.
        """
        h = self.genesis_header

        # Build kwargs for Header
        kwargs = {
            "parent_hash": Hash32(hex_to_bytes(h["parentHash"])),
            "ommers_hash": Hash32(hex_to_bytes(h["uncleHash"])),
            "coinbase": fork.hex_to_address(h["coinbase"]),
            "state_root": fork.hex_to_root(h["stateRoot"]),
            "transactions_root": fork.hex_to_root(h["transactionsTrie"]),
            "receipt_root": fork.hex_to_root(h["receiptTrie"]),
            "bloom": fork.Bloom(hex_to_bytes(h["bloom"])),
            "difficulty": hex_to_uint(h["difficulty"]),
            "number": hex_to_uint(h["number"]),
            "gas_limit": hex_to_uint(h["gasLimit"]),
            "gas_used": hex_to_uint(h["gasUsed"]),
            "timestamp": hex_to_u256(h["timestamp"]),
            "extra_data": hex_to_bytes(h["extraData"]),
            "prev_randao": Bytes32(hex_to_bytes(h["mixHash"])),
            "nonce": hex_to_bytes(h["nonce"]),
        }

        # Add fork-specific fields
        if "baseFeePerGas" in h:
            kwargs["base_fee_per_gas"] = hex_to_uint(h["baseFeePerGas"])

        if "withdrawalsRoot" in h and fork.has_withdrawal:
            kwargs["withdrawals_root"] = fork.hex_to_root(h["withdrawalsRoot"])

        if "blobGasUsed" in h and fork.has_beacon_roots_address:
            kwargs["blob_gas_used"] = hex_to_u64(h["blobGasUsed"])
            kwargs["excess_blob_gas"] = hex_to_u64(h["excessBlobGas"])
            kwargs["parent_beacon_block_root"] = Hash32(
                hex_to_bytes(h["parentBeaconBlockRoot"])
            )

        return fork.Header(**kwargs)

    def get_genesis_hash(self) -> Hash32:
        """Get the genesis block hash."""
        return Hash32(hex_to_bytes(self.genesis_header["hash"]))

    def get_payload_data(self, index: int) -> Tuple[Dict[str, Any], List[Any]]:
        """
        Get payload data at given index.

        Returns
        -------
        payload_data :
            The execution payload JSON.
        extra_params :
            Additional params (blob hashes, parent beacon root for V3+).
        """
        payload = self.engine_payloads[index]
        params = payload.get("params", [])
        payload_data = params[0] if params else {}
        extra_params = params[1:] if len(params) > 1 else []
        return payload_data, extra_params

    def get_payload_count(self) -> int:
        """Get number of payloads in the fixture."""
        return len(self.engine_payloads)

    def load_pre_state(self, t8n: "T8N") -> None:
        """
        Load the pre-state into T8N's alloc.

        This populates the state with accounts from the fixture's `pre` field.
        """
        fork = t8n.fork
        state = fork.State()

        for address_hex, account_data in self.pre_state.items():
            address = fork.hex_to_address(address_hex)

            nonce = hex_to_uint(account_data.get("nonce", "0x0"))
            balance = hex_to_u256(account_data.get("balance", "0x0"))
            code = hex_to_bytes(account_data.get("code", "0x"))

            account = fork.Account(
                nonce=nonce,
                balance=balance,
                code=code,
            )
            fork.set_account(state, address, account)

            # Set storage
            storage = account_data.get("storage", {})
            for slot_hex, value_hex in storage.items():
                slot = hex_to_u256(slot_hex)
                value = hex_to_u256(value_hex)
                if value != 0:
                    fork.set_storage(state, address, slot, value)

        return state
