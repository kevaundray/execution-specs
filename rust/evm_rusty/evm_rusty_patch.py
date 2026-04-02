"""
Monkey-patch the EELS interpreter with the Rust inner loop.

Usage:
    from evm_rusty_patch import patch_interpreter
    patch_interpreter("osaka")
"""

from importlib import import_module
from typing import Set

from ethereum_types.bytes import Bytes
from ethereum_types.numeric import U256, Uint

import evm_rusty


def patch_interpreter(fork_name: str = "osaka") -> None:
    """Replace process_message with a Rust-accelerated version."""
    interp_mod = import_module(f"ethereum.forks.{fork_name}.vm.interpreter")
    vm_mod = import_module(f"ethereum.forks.{fork_name}.vm")
    runtime_mod = import_module(f"ethereum.forks.{fork_name}.vm.runtime")
    state_mod = import_module(f"ethereum.forks.{fork_name}.state")
    gas_mod = import_module(f"ethereum.forks.{fork_name}.vm.gas")
    exceptions_mod = import_module(
        f"ethereum.forks.{fork_name}.vm.exceptions"
    )
    from ethereum.trace import (
        EvmStop,
        OpEnd,
        OpException,
        OpStart,
        PrecompileEnd,
        PrecompileStart,
        TransactionEnd,
        evm_trace,
    )

    # Get references to exception classes
    exc_mod = import_module(f"ethereum.forks.{fork_name}.vm.exceptions")
    ExceptionalHalt = exc_mod.ExceptionalHalt
    Revert = exc_mod.Revert
    OutOfGasError = exc_mod.OutOfGasError
    InvalidJumpDestError = exc_mod.InvalidJumpDestError
    StackUnderflowError = exc_mod.StackUnderflowError
    StackOverflowError = exc_mod.StackOverflowError
    InvalidOpcode = exc_mod.InvalidOpcode

    Ops = getattr(
        import_module(f"ethereum.forks.{fork_name}.vm.instructions"),
        "Ops",
    )
    op_implementation = getattr(
        import_module(f"ethereum.forks.{fork_name}.vm.instructions"),
        "op_implementation",
    )

    PRE_COMPILED_CONTRACTS = getattr(
        import_module(
            f"ethereum.forks.{fork_name}.vm.precompiled_contracts.mapping"
        ),
        "PRE_COMPILED_CONTRACTS",
    )

    get_valid_jump_destinations = runtime_mod.get_valid_jump_destinations
    begin_transaction = state_mod.begin_transaction
    commit_transaction = state_mod.commit_transaction
    rollback_transaction = state_mod.rollback_transaction
    move_ether = state_mod.move_ether
    get_storage = state_mod.get_storage
    get_storage_original = state_mod.get_storage_original
    set_storage = state_mod.set_storage
    get_transient_storage = state_mod.get_transient_storage
    set_transient_storage = state_mod.set_transient_storage
    charge_gas = gas_mod.charge_gas

    from ethereum_types.bytes import Bytes32

    Evm = vm_mod.Evm
    STACK_DEPTH_LIMIT = Uint(1024)

    original_process_message = interp_mod.process_message

    def _u256_to_be_bytes(val) -> bytes:
        """Convert a U256/Uint/int to 32 big-endian bytes."""
        return int(val).to_bytes(32, "big")

    def _addr_to_bytes(addr) -> bytes:
        """Convert an address to 20 bytes."""
        if isinstance(addr, bytes):
            return addr.ljust(20, b"\x00")[-20:]
        return bytes(addr).ljust(20, b"\x00")[-20:]

    def rust_process_message(message) -> "Evm":
        """Rust-accelerated process_message."""
        state = message.block_env.state
        if message.depth > STACK_DEPTH_LIMIT:
            from ethereum.forks.osaka.vm.instructions.exceptions import (
                StackDepthLimitError,
            )

            raise StackDepthLimitError("Stack depth limit reached")

        transient_storage = message.tx_env.transient_storage
        code = message.code
        valid_jump_destinations = {
            Uint(v)
            for v in evm_rusty.valid_jump_destinations(bytes(code))
        }
        evm = Evm(
            pc=Uint(0),
            stack=[],
            memory=bytearray(),
            code=code,
            gas_left=message.gas,
            valid_jump_destinations=valid_jump_destinations,
            logs=(),
            refund_counter=0,
            running=True,
            message=message,
            output=b"",
            accounts_to_delete=set(),
            return_data=b"",
            error=None,
            accessed_addresses=message.accessed_addresses,
            accessed_storage_keys=message.accessed_storage_keys,
        )

        begin_transaction(state, transient_storage)

        if message.should_transfer_value and message.value != 0:
            move_ether(
                state,
                message.caller,
                message.current_target,
                message.value,
            )

        try:
            if (
                evm.message.code_address in PRE_COMPILED_CONTRACTS
                and not message.disable_precompiles
            ):
                evm_trace(evm, PrecompileStart(evm.message.code_address))
                PRE_COMPILED_CONTRACTS[evm.message.code_address](evm)
                evm_trace(evm, PrecompileEnd())
            else:
                # Prepare context for Rust — computed once
                block_env = message.block_env
                tx_env = message.tx_env

                # Get self balance for SELFBALANCE opcode
                get_account = getattr(
                    import_module(f"ethereum.forks.{fork_name}.state"),
                    "get_account",
                )
                self_balance_val = get_account(
                    state, message.current_target
                ).balance

                # Compute jump destinations in Rust (fast)
                code_bytes = bytes(code)
                jumpdest_list = evm_rusty.valid_jump_destinations(
                    code_bytes
                )

                # Pre-compute all context bytes once
                ctx_gas_price = _u256_to_be_bytes(tx_env.gas_price)
                ctx_origin = _addr_to_bytes(tx_env.origin)
                ctx_caller = _addr_to_bytes(message.caller)
                ctx_callvalue = _u256_to_be_bytes(message.value)
                ctx_calldata = bytes(message.data)
                ctx_block_number = _u256_to_be_bytes(block_env.number)
                ctx_coinbase = _addr_to_bytes(block_env.coinbase)
                ctx_timestamp = _u256_to_be_bytes(block_env.time)
                ctx_prev_randao = bytes(block_env.prev_randao)
                ctx_gas_limit = _u256_to_be_bytes(
                    block_env.block_gas_limit
                )
                ctx_chain_id = int(block_env.chain_id)
                ctx_base_fee = _u256_to_be_bytes(
                    block_env.base_fee_per_gas
                )
                ctx_current_address = _addr_to_bytes(
                    message.current_target
                )
                ctx_self_balance = _u256_to_be_bytes(self_balance_val)

                # Build state callback for storage ops
                def state_cb(op, key_bytes, value_bytes=None):
                    key = Bytes32(key_bytes)
                    if op == "sload":
                        val = get_storage(
                            state, message.current_target, key
                        )
                        return int(val).to_bytes(32, "big")
                    elif op == "sstore_info":
                        orig = get_storage_original(
                            state, message.current_target, key
                        )
                        curr = get_storage(
                            state, message.current_target, key
                        )
                        return (
                            int(orig).to_bytes(32, "big"),
                            int(curr).to_bytes(32, "big"),
                        )
                    elif op == "sstore_set":
                        val = U256.from_be_bytes(value_bytes)
                        set_storage(
                            state,
                            message.current_target,
                            key,
                            val,
                        )
                        return b""
                    elif op == "tload":
                        val = get_transient_storage(
                            transient_storage,
                            message.current_target,
                            key,
                        )
                        return int(val).to_bytes(32, "big")
                    elif op == "tstore":
                        val = U256.from_be_bytes(value_bytes)
                        set_transient_storage(
                            transient_storage,
                            message.current_target,
                            key,
                            val,
                        )
                        return b""
                    return b""

                # Initial state
                stack_bytes = []
                memory_bytes = bytes(evm.memory)
                pc = 0
                gas_left_val = int(evm.gas_left)

                while True:
                    result = evm_rusty.execute_inner_loop(
                        code=code_bytes,
                        pc_start=pc,
                        gas_left_start=gas_left_val,
                        stack_bytes=stack_bytes,
                        memory_bytes=memory_bytes,
                        valid_jumpdests=jumpdest_list,
                        gas_price=ctx_gas_price,
                        origin=ctx_origin,
                        caller=ctx_caller,
                        callvalue=ctx_callvalue,
                        calldata=ctx_calldata,
                        code_for_env=code_bytes,
                        return_data=bytes(evm.return_data),
                        block_number=ctx_block_number,
                        coinbase=ctx_coinbase,
                        timestamp=ctx_timestamp,
                        prev_randao=ctx_prev_randao,
                        gas_limit=ctx_gas_limit,
                        chain_id=ctx_chain_id,
                        base_fee=ctx_base_fee,
                        current_address=ctx_current_address,
                        self_balance=ctx_self_balance,
                        state_callback=state_cb,
                        is_static=message.is_static,
                    )

                    (
                        new_pc,
                        new_gas_left,
                        new_stack_bytes,
                        new_memory,
                        still_running,
                        fallback_op,
                        err_str,
                        rust_refund,
                    ) = result
                    evm.refund_counter += rust_refund

                    if err_str is not None:
                        # Map error to exception — set EVM state first
                        evm.pc = Uint(new_pc)
                        evm.gas_left = Uint(0)
                        evm.running = False
                        if "OutOfGas" in err_str:
                            raise OutOfGasError
                        elif "StackUnderflow" in err_str:
                            raise StackUnderflowError
                        elif "StackOverflow" in err_str:
                            raise StackOverflowError
                        elif "InvalidJumpDest" in err_str:
                            raise InvalidJumpDestError
                        elif "InvalidOpcode" in err_str:
                            raise InvalidOpcode(
                                int(
                                    err_str.split("0x")[1].rstrip(")"),
                                    16,
                                )
                            )
                        else:
                            raise ExceptionalHalt()

                    if fallback_op != 0:
                        # Rust hit an opcode it can't handle.
                        # Sync EVM state, run in Python, resume.
                        evm.pc = Uint(new_pc)
                        evm.gas_left = Uint(new_gas_left)
                        evm.stack = [
                            U256.from_be_bytes(b)
                            for b in new_stack_bytes
                        ]
                        evm.memory = bytearray(new_memory)
                        evm.running = still_running

                        try:
                            op = Ops(fallback_op)
                        except ValueError:
                            raise InvalidOpcode(fallback_op)

                        evm_trace(evm, OpStart(op))
                        op_implementation[op](evm)
                        evm_trace(evm, OpEnd())

                        # Prepare to re-enter Rust
                        pc = int(evm.pc)
                        gas_left_val = int(evm.gas_left)
                        stack_bytes = [
                            int(v).to_bytes(32, "big")
                            for v in evm.stack
                        ]
                        memory_bytes = bytes(evm.memory)

                        if not evm.running or pc >= len(code):
                            break
                        continue

                    # No fallback, no error — Rust finished cleanly.
                    # Skip stack deserialization — it's not needed
                    # after execution completes (callers only read
                    # gas_left, output, logs, error).
                    evm.pc = Uint(new_pc)
                    evm.gas_left = Uint(new_gas_left)
                    evm.memory = bytearray(new_memory)
                    evm.running = still_running
                    break

                if evm.running and evm.pc >= Uint(len(code)):
                    evm_trace(evm, EvmStop(Ops.STOP))

        except ExceptionalHalt as error:
            evm_trace(evm, OpException(error))
            evm.gas_left = Uint(0)
            evm.output = b""
            evm.error = error
        except Revert as error:
            evm_trace(evm, OpException(error))
            evm.error = error

        if evm.error:
            rollback_transaction(state, transient_storage)
        else:
            commit_transaction(state, transient_storage)
        return evm

    # Rust-accelerated jump destination analysis
    def rust_get_valid_jump_destinations(code):
        """Compute valid jump destinations using Rust."""
        result = evm_rusty.valid_jump_destinations(bytes(code))
        return {Uint(v) for v in result}

    # Apply patches
    interp_mod.process_message = rust_process_message
    runtime_mod.get_valid_jump_destinations = (
        rust_get_valid_jump_destinations
    )
    print("[evm_rusty] Patched process_message for", fork_name)
