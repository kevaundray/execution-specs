"""
Test internal CALL/STATICCALL with barely enough gas for intrinsic cost.

The transaction gas limit is just above the intrinsic cost, leaving almost
no gas for execution. The internal call runs out of gas and its state
changes are reverted, but the transaction itself is still processed.
"""

import pytest
from execution_testing import (
    Account,
    Alloc,
    Op,
    StateTestFiller,
    Transaction,
)


@pytest.mark.ported_from(
    [
        "https://github.com/ethereum/tests/blob/v13.3/src/GeneralStateTestsFiller/stTransactionTest/InternalCallHittingGasLimitFiller.json",  # noqa: E501
    ],
)
@pytest.mark.valid_from("Cancun")
def test_internal_call_hitting_gas_limit(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """
    CALL to a contract with insufficient gas for its SSTORE.

    Contract B calls contract C with gas=5000 and value=1.
    Contract C attempts SSTORE(1, 55) which costs more than the
    available gas. The inner call fails, leaving C with zero balance
    and empty storage.
    """
    callee = pre.deploy_contract(code=Op.SSTORE(1, 55))
    caller = pre.deploy_contract(
        code=Op.CALL(gas=5000, address=callee, value=1),
        balance=1_000_000,
    )
    sender = pre.fund_eoa(amount=1_000_000_000)

    tx = Transaction(
        sender=sender,
        to=caller,
        gas_limit=21_100,
        gas_price=10,
        value=10,
    )

    state_test(
        pre=pre,
        post={callee: Account(balance=0, storage={})},
        tx=tx,
    )


@pytest.mark.ported_from(
    [
        "https://github.com/ethereum/tests/blob/v13.3/src/GeneralStateTestsFiller/stStaticCall/static_InternalCallHittingGasLimitFiller.json",  # noqa: E501
    ],
)
@pytest.mark.valid_from("Cancun")
def test_static_internal_call_hitting_gas_limit(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """
    STATICCALL to a contract with insufficient gas for its loop.

    Contract B static-calls contract C with gas=5000. Contract C
    loops doing EXTCODESIZE, consuming all forwarded gas. The inner
    call fails but the transaction processes normally.
    """
    callee = pre.deploy_contract(
        code=Op.EXTCODESIZE(1) + Op.POP,
    )
    caller = pre.deploy_contract(
        code=Op.STATICCALL(gas=5000, address=callee),
        balance=1_000_000,
    )
    sender = pre.fund_eoa(amount=1_000_000)

    tx = Transaction(
        sender=sender,
        to=caller,
        gas_limit=21_100,
        gas_price=10,
        value=10,
    )

    state_test(
        pre=pre,
        post={sender: Account(nonce=1)},
        tx=tx,
    )
