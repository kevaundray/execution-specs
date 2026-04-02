use pyo3::prelude::*;
use pyo3::types::PyBytes;
use ruint::aliases::U256;
use std::collections::HashSet;

/// Errors that can happen during execution.
enum EvmError {
    OutOfGas,
    StackUnderflow,
    StackOverflow,
    InvalidJumpDest,
    InvalidOpcode(u8),
    CallbackError(String),
}

impl EvmError {
    fn to_string(&self) -> String {
        match self {
            EvmError::OutOfGas => "OutOfGasError".into(),
            EvmError::StackUnderflow => "StackUnderflowError".into(),
            EvmError::StackOverflow => "StackOverflowError".into(),
            EvmError::InvalidJumpDest => "InvalidJumpDestError".into(),
            EvmError::InvalidOpcode(op) => format!("InvalidOpcode(0x{:02x})", op),
            EvmError::CallbackError(msg) => format!("CallbackError: {}", msg),
        }
    }
}

impl From<PyErr> for EvmError {
    fn from(err: PyErr) -> Self {
        EvmError::CallbackError(err.to_string())
    }
}

const STACK_LIMIT: usize = 1024;
const U256_MAX: U256 = U256::MAX;
// 2^255
const U255_CEIL: U256 = U256::from_limbs([0, 0, 0, 0x8000000000000000]);

/// Gas costs
const GAS_ZERO: u64 = 0;
const GAS_JUMPDEST: u64 = 1;
const GAS_BASE: u64 = 2;
const GAS_VERY_LOW: u64 = 3;
const GAS_LOW: u64 = 5;
const GAS_MID: u64 = 8;
const GAS_HIGH: u64 = 10;
const GAS_EXP: u64 = 10;
const GAS_EXP_PER_BYTE: u64 = 50;
const GAS_MEMORY: u64 = 3;
const GAS_COLD_SLOAD: u64 = 2100;
const GAS_WARM_ACCESS: u64 = 100;
const GAS_STORAGE_SET: u64 = 20000;
const GAS_STORAGE_UPDATE: u64 = 5000;
const GAS_CALL_STIPEND: u64 = 2300;
const REFUND_STORAGE_CLEAR: i64 = 4800;

#[inline(always)]
fn charge_gas(gas_left: &mut u64, cost: u64) -> Result<(), EvmError> {
    if *gas_left < cost {
        Err(EvmError::OutOfGas)
    } else {
        *gas_left -= cost;
        Ok(())
    }
}

#[inline(always)]
fn stack_pop(stack: &mut Vec<U256>) -> Result<U256, EvmError> {
    stack.pop().ok_or(EvmError::StackUnderflow)
}

#[inline(always)]
fn stack_push(stack: &mut Vec<U256>, val: U256) -> Result<(), EvmError> {
    if stack.len() >= STACK_LIMIT {
        Err(EvmError::StackOverflow)
    } else {
        stack.push(val);
        Ok(())
    }
}

/// Convert U256 to signed i256 representation (as a big int).
/// Returns (abs_value, is_negative).
#[inline]
fn to_signed(val: U256) -> (U256, bool) {
    if val >= U255_CEIL {
        // Negative: two's complement
        let abs = (!val).wrapping_add(U256::from(1));
        (abs, true)
    } else {
        (val, false)
    }
}

/// Convert signed representation back to U256.
#[inline]
fn from_signed(abs: U256, negative: bool) -> U256 {
    if negative {
        (!abs).wrapping_add(U256::from(1))
    } else {
        abs
    }
}

/// Calculate memory gas cost.
fn memory_gas_cost(size_bytes: u64) -> u64 {
    let size_words = (size_bytes + 31) / 32;
    size_words * GAS_MEMORY + (size_words * size_words) / 512
}

/// Extend memory if needed, charge gas.
fn extend_memory(
    memory: &mut Vec<u8>,
    gas_left: &mut u64,
    offset: U256,
    size: U256,
) -> Result<(), EvmError> {
    if size.is_zero() {
        return Ok(());
    }

    let offset_u64: u64 = offset.try_into().map_err(|_| EvmError::OutOfGas)?;
    let size_u64: u64 = size.try_into().map_err(|_| EvmError::OutOfGas)?;
    let end = offset_u64.checked_add(size_u64).ok_or(EvmError::OutOfGas)?;

    let current_size = memory.len() as u64;
    let new_size = ((end + 31) / 32) * 32;

    if new_size > current_size {
        let old_cost = memory_gas_cost(current_size);
        let new_cost = memory_gas_cost(new_size);
        charge_gas(gas_left, new_cost - old_cost)?;
        memory.resize(new_size as usize, 0);
    }
    Ok(())
}

/// The main Rust EVM inner loop.
///
/// Executes pure-compute opcodes in Rust. When it hits a
/// state-touching or unsupported opcode, it returns control to Python
/// with the current state so Python can handle it.
///
/// Returns: (pc, gas_left, stack_as_be_bytes, memory, running, reverted, fallback_op, error)
#[pyfunction]
fn execute_inner_loop(
    py: Python<'_>,
    code: &[u8],
    pc_start: u64,
    gas_left_start: u64,
    stack_bytes: Vec<[u8; 32]>,
    memory_bytes: &[u8],
    valid_jumpdests: Vec<u64>,
    // Context values (read-only, passed from Message)
    gas_price: &[u8],
    origin: &[u8],
    caller: &[u8],
    callvalue: &[u8],
    calldata: &[u8],
    code_for_env: &[u8], // code bytes for CODESIZE/CODECOPY
    return_data: &[u8],
    block_number: &[u8],
    coinbase: &[u8],
    timestamp: &[u8],
    prev_randao: &[u8],
    gas_limit: &[u8],
    chain_id: u64,
    base_fee: &[u8],
    current_address: &[u8],
    self_balance: &[u8],
    // Callback for state operations: fn(op, key, value?) -> result_bytes
    // op: "sload"|"sstore"|"tload"|"tstore"|"sload_check_warm"|"sstore_info"
    state_callback: Option<&Bound<'_, PyAny>>,
    is_static: bool,
) -> PyResult<PyObject> {

    // Helper to pad a byte slice to 32 bytes (BE)
    fn to_u256(bytes: &[u8]) -> U256 {
        let mut buf = [0u8; 32];
        let start = 32usize.saturating_sub(bytes.len());
        let copy_len = bytes.len().min(32);
        buf[start..start + copy_len].copy_from_slice(&bytes[..copy_len]);
        U256::from_be_bytes(buf)
    }
    fn to_addr(bytes: &[u8]) -> [u8; 20] {
        let mut buf = [0u8; 20];
        let copy_len = bytes.len().min(20);
        let start = 20usize.saturating_sub(bytes.len());
        buf[start..start + copy_len].copy_from_slice(&bytes[..copy_len]);
        buf
    }

    let origin_bytes = to_addr(origin);
    let caller_bytes = to_addr(caller);
    let coinbase_bytes = to_addr(coinbase);
    let current_address_bytes = to_addr(current_address);

    let mut pc = pc_start as usize;
    let mut gas_left = gas_left_start;
    let mut stack: Vec<U256> = stack_bytes
        .iter()
        .map(|b| U256::from_be_bytes(*b))
        .collect();
    let mut memory: Vec<u8> = memory_bytes.to_vec();

    // Convert valid jump destinations to a HashSet for O(1) lookup
    let jumpdests: HashSet<usize> = valid_jumpdests.iter().map(|v| *v as usize).collect();

    // Pre-convert context values
    let ctx_gas_price = to_u256(gas_price);
    let ctx_callvalue = to_u256(callvalue);
    let ctx_block_number = to_u256(block_number);
    let ctx_timestamp = to_u256(timestamp);
    let ctx_prev_randao = to_u256(prev_randao);
    let ctx_gas_limit = to_u256(gas_limit);
    let ctx_chain_id = U256::from(chain_id);
    let ctx_base_fee = to_u256(base_fee);
    let ctx_self_balance = to_u256(self_balance);

    let mut running = true;
    let mut refund_counter: i64 = 0;
    // Track accessed storage keys as (address, key) pairs for warm/cold gas
    let mut accessed_storage_keys: HashSet<([u8; 20], [u8; 32])> = HashSet::new();
    let mut fallback_op: u8 = 0;
    let mut error: Option<String> = None;

    let code_len = code.len();

    let result = (|| -> Result<(), EvmError> {
        while running && pc < code_len {
            let op = code[pc];

            match op {
                // ============================================
                // STOP
                // ============================================
                0x00 => {
                    running = false;
                    pc += 1;
                }

                // ============================================
                // Arithmetic: ADD, MUL, SUB, DIV, SDIV, MOD, SMOD, ADDMOD, MULMOD, EXP, SIGNEXTEND
                // ============================================
                0x01 => {
                    // ADD
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, a.wrapping_add(b))?;
                    pc += 1;
                }
                0x02 => {
                    // MUL
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;
                    stack_push(&mut stack, a.wrapping_mul(b))?;
                    pc += 1;
                }
                0x03 => {
                    // SUB
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, a.wrapping_sub(b))?;
                    pc += 1;
                }
                0x04 => {
                    // DIV
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;
                    let result = if b.is_zero() {
                        U256::ZERO
                    } else {
                        a / b
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x05 => {
                    // SDIV
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;

                    let result = if b.is_zero() {
                        U256::ZERO
                    } else {
                        let (a_abs, a_neg) = to_signed(a);
                        let (b_abs, b_neg) = to_signed(b);

                        if a == U255_CEIL && b == U256_MAX {
                            // Special case: -2^255 / -1 = -2^255 (overflow)
                            U255_CEIL
                        } else {
                            let q = a_abs / b_abs;
                            from_signed(q, a_neg != b_neg && !q.is_zero())
                        }
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x06 => {
                    // MOD
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;
                    let result = if b.is_zero() {
                        U256::ZERO
                    } else {
                        a % b
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x07 => {
                    // SMOD
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;

                    let result = if b.is_zero() {
                        U256::ZERO
                    } else {
                        let (a_abs, a_neg) = to_signed(a);
                        let (b_abs, _) = to_signed(b);
                        let r = a_abs % b_abs;
                        from_signed(r, a_neg && !r.is_zero())
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x08 => {
                    // ADDMOD
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    let n = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_MID)?;

                    let result = if n.is_zero() {
                        U256::ZERO
                    } else {
                        a.add_mod(b, n)
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x09 => {
                    // MULMOD
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    let n = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_MID)?;

                    let result = if n.is_zero() {
                        U256::ZERO
                    } else {
                        a.mul_mod(b, n)
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x0A => {
                    // EXP
                    let base = stack_pop(&mut stack)?;
                    let exponent = stack_pop(&mut stack)?;
                    let exp_bytes = if exponent.is_zero() {
                        0u64
                    } else {
                        ((exponent.bit_len() + 7) / 8) as u64
                    };
                    charge_gas(&mut gas_left, GAS_EXP + GAS_EXP_PER_BYTE * exp_bytes)?;

                    let result = base.pow(exponent);
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x0B => {
                    // SIGNEXTEND
                    let byte_num = stack_pop(&mut stack)?;
                    let value = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;

                    let result = if byte_num >= U256::from(31) {
                        value
                    } else {
                        let bit = byte_num * U256::from(8) + U256::from(7);
                        let bit_usize: usize = bit.try_into().unwrap_or(255);
                        let mask = (U256::from(1) << bit) - U256::from(1);
                        if value.bit(bit_usize) {
                            value | !mask
                        } else {
                            value & mask
                        }
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }

                // ============================================
                // Comparison: LT, GT, SLT, SGT, EQ, ISZERO
                // ============================================
                0x10 => {
                    // LT
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, if a < b { U256::from(1) } else { U256::ZERO })?;
                    pc += 1;
                }
                0x11 => {
                    // GT
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, if a > b { U256::from(1) } else { U256::ZERO })?;
                    pc += 1;
                }
                0x12 => {
                    // SLT
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let (a_abs, a_neg) = to_signed(a);
                    let (b_abs, b_neg) = to_signed(b);
                    let lt = match (a_neg, b_neg) {
                        (true, false) => true,
                        (false, true) => false,
                        (false, false) => a_abs < b_abs,
                        (true, true) => a_abs > b_abs,
                    };
                    stack_push(&mut stack, if lt { U256::from(1) } else { U256::ZERO })?;
                    pc += 1;
                }
                0x13 => {
                    // SGT
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let (a_abs, a_neg) = to_signed(a);
                    let (b_abs, b_neg) = to_signed(b);
                    let gt = match (a_neg, b_neg) {
                        (true, false) => false,
                        (false, true) => true,
                        (false, false) => a_abs > b_abs,
                        (true, true) => a_abs < b_abs,
                    };
                    stack_push(&mut stack, if gt { U256::from(1) } else { U256::ZERO })?;
                    pc += 1;
                }
                0x14 => {
                    // EQ
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, if a == b { U256::from(1) } else { U256::ZERO })?;
                    pc += 1;
                }
                0x15 => {
                    // ISZERO
                    let a = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(
                        &mut stack,
                        if a.is_zero() {
                            U256::from(1)
                        } else {
                            U256::ZERO
                        },
                    )?;
                    pc += 1;
                }

                // ============================================
                // Bitwise: AND, OR, XOR, NOT, BYTE, SHL, SHR, SAR, CLZ
                // ============================================
                0x16 => {
                    // AND
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, a & b)?;
                    pc += 1;
                }
                0x17 => {
                    // OR
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, a | b)?;
                    pc += 1;
                }
                0x18 => {
                    // XOR
                    let a = stack_pop(&mut stack)?;
                    let b = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, a ^ b)?;
                    pc += 1;
                }
                0x19 => {
                    // NOT
                    let a = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    stack_push(&mut stack, !a)?;
                    pc += 1;
                }
                0x1A => {
                    // BYTE
                    let i = stack_pop(&mut stack)?;
                    let x = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let result = if i >= U256::from(32) {
                        U256::ZERO
                    } else {
                        let byte_index: usize = i.try_into().unwrap();
                        let bytes = x.to_be_bytes::<32>();
                        U256::from(bytes[byte_index])
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x1B => {
                    // SHL
                    let shift = stack_pop(&mut stack)?;
                    let value = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let result = if shift >= U256::from(256) {
                        U256::ZERO
                    } else {
                        let s: usize = shift.try_into().unwrap();
                        value << s
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x1C => {
                    // SHR
                    let shift = stack_pop(&mut stack)?;
                    let value = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let result = if shift >= U256::from(256) {
                        U256::ZERO
                    } else {
                        let s: usize = shift.try_into().unwrap();
                        value >> s
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x1D => {
                    // SAR
                    let shift = stack_pop(&mut stack)?;
                    let value = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let (abs, neg) = to_signed(value);
                    let result = if shift >= U256::from(256) {
                        if neg {
                            U256_MAX
                        } else {
                            U256::ZERO
                        }
                    } else {
                        let s: usize = shift.try_into().unwrap();
                        if neg {
                            // Arithmetic right shift for negative
                            let shifted = abs >> s;
                            if shifted.is_zero() {
                                U256_MAX
                            } else {
                                from_signed(shifted, true)
                            }
                        } else {
                            abs >> s
                        }
                    };
                    stack_push(&mut stack, result)?;
                    pc += 1;
                }
                0x1E => {
                    // CLZ (count leading zeros)
                    let x = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_LOW)?;
                    let bl = x.bit_len();
                    stack_push(&mut stack, U256::from(256 - bl))?;
                    pc += 1;
                }

                // ============================================
                // Environmental: ADDRESS, ORIGIN, CALLER, CALLVALUE,
                //   CALLDATALOAD, CALLDATASIZE, CALLDATACOPY,
                //   CODESIZE, CODECOPY, GASPRICE,
                //   RETURNDATASIZE, RETURNDATACOPY
                // ============================================
                0x30 => {
                    // ADDRESS
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    let mut val = [0u8; 32];
                    val[12..32].copy_from_slice(&current_address_bytes);
                    stack_push(&mut stack, U256::from_be_bytes(val))?;
                    pc += 1;
                }
                0x32 => {
                    // ORIGIN
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    let mut val = [0u8; 32];
                    val[12..32].copy_from_slice(&origin_bytes);
                    stack_push(&mut stack, U256::from_be_bytes(val))?;
                    pc += 1;
                }
                0x33 => {
                    // CALLER
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    let mut val = [0u8; 32];
                    val[12..32].copy_from_slice(&caller_bytes);
                    stack_push(&mut stack, U256::from_be_bytes(val))?;
                    pc += 1;
                }
                0x34 => {
                    // CALLVALUE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_callvalue)?;
                    pc += 1;
                }
                0x35 => {
                    // CALLDATALOAD
                    let offset = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let off: usize = offset.try_into().unwrap_or(usize::MAX);
                    let mut result = [0u8; 32];
                    for i in 0..32 {
                        if off + i < calldata.len() {
                            result[i] = calldata[off + i];
                        }
                    }
                    stack_push(&mut stack, U256::from_be_bytes(result))?;
                    pc += 1;
                }
                0x36 => {
                    // CALLDATASIZE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::from(calldata.len()))?;
                    pc += 1;
                }
                0x37 => {
                    // CALLDATACOPY
                    let dest_offset = stack_pop(&mut stack)?;
                    let offset = stack_pop(&mut stack)?;
                    let size = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let size_u64: u64 = size.try_into().map_err(|_| EvmError::OutOfGas)?;
                    let words = (size_u64 + 31) / 32;
                    charge_gas(&mut gas_left, GAS_VERY_LOW * words)?;
                    if !size.is_zero() {
                        extend_memory(&mut memory, &mut gas_left, dest_offset, size)?;
                        let dest: usize = dest_offset.try_into().map_err(|_| EvmError::OutOfGas)?;
                        let src: usize = offset.try_into().unwrap_or(usize::MAX);
                        let sz: usize = size.try_into().map_err(|_| EvmError::OutOfGas)?;
                        for i in 0..sz {
                            memory[dest + i] = if src + i < calldata.len() {
                                calldata[src + i]
                            } else {
                                0
                            };
                        }
                    }
                    pc += 1;
                }
                0x38 => {
                    // CODESIZE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::from(code_for_env.len()))?;
                    pc += 1;
                }
                0x3A => {
                    // GASPRICE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_gas_price)?;
                    pc += 1;
                }
                0x3D => {
                    // RETURNDATASIZE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::from(return_data.len()))?;
                    pc += 1;
                }

                // ============================================
                // Block context: BLOCKHASH(fallback), COINBASE, TIMESTAMP,
                //   NUMBER, PREVRANDAO, GASLIMIT, CHAINID, SELFBALANCE,
                //   BASEFEE
                // ============================================
                0x41 => {
                    // COINBASE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    let mut val = [0u8; 32];
                    val[12..32].copy_from_slice(&coinbase_bytes);
                    stack_push(&mut stack, U256::from_be_bytes(val))?;
                    pc += 1;
                }
                0x42 => {
                    // TIMESTAMP
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_timestamp)?;
                    pc += 1;
                }
                0x43 => {
                    // NUMBER
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_block_number)?;
                    pc += 1;
                }
                0x44 => {
                    // PREVRANDAO
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_prev_randao)?;
                    pc += 1;
                }
                0x45 => {
                    // GASLIMIT
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_gas_limit)?;
                    pc += 1;
                }
                0x46 => {
                    // CHAINID
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_chain_id)?;
                    pc += 1;
                }
                0x47 => {
                    // SELFBALANCE
                    charge_gas(&mut gas_left, GAS_LOW)?;
                    stack_push(&mut stack, ctx_self_balance)?;
                    pc += 1;
                }
                0x48 => {
                    // BASEFEE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, ctx_base_fee)?;
                    pc += 1;
                }

                // ============================================
                // Memory: MLOAD, MSTORE, MSTORE8, MSIZE
                // ============================================
                0x51 => {
                    // MLOAD
                    let offset = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    extend_memory(
                        &mut memory,
                        &mut gas_left,
                        offset,
                        U256::from(32),
                    )?;
                    let off: usize = offset.try_into().map_err(|_| EvmError::OutOfGas)?;
                    let mut bytes = [0u8; 32];
                    bytes.copy_from_slice(&memory[off..off + 32]);
                    stack_push(&mut stack, U256::from_be_bytes(bytes))?;
                    pc += 1;
                }
                0x52 => {
                    // MSTORE
                    let offset = stack_pop(&mut stack)?;
                    let value = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    extend_memory(
                        &mut memory,
                        &mut gas_left,
                        offset,
                        U256::from(32),
                    )?;
                    let off: usize = offset.try_into().map_err(|_| EvmError::OutOfGas)?;
                    let bytes = value.to_be_bytes::<32>();
                    memory[off..off + 32].copy_from_slice(&bytes);
                    pc += 1;
                }
                0x53 => {
                    // MSTORE8
                    let offset = stack_pop(&mut stack)?;
                    let value = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    extend_memory(
                        &mut memory,
                        &mut gas_left,
                        offset,
                        U256::from(1),
                    )?;
                    let off: usize = offset.try_into().map_err(|_| EvmError::OutOfGas)?;
                    let byte_val = value.byte(0); // least significant byte
                    memory[off] = byte_val;
                    pc += 1;
                }
                0x59 => {
                    // MSIZE
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::from(memory.len()))?;
                    pc += 1;
                }

                // ============================================
                // Stack: POP
                // ============================================
                0x50 => {
                    // POP
                    stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    pc += 1;
                }

                // ============================================
                // Control flow: JUMP, JUMPI, PC, GAS, JUMPDEST
                // ============================================
                0x56 => {
                    // JUMP
                    let dest = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_MID)?;
                    let dest_usize: usize =
                        dest.try_into().map_err(|_| EvmError::InvalidJumpDest)?;
                    if !jumpdests.contains(&dest_usize) {
                        return Err(EvmError::InvalidJumpDest);
                    }
                    pc = dest_usize;
                }
                0x57 => {
                    // JUMPI
                    let dest = stack_pop(&mut stack)?;
                    let cond = stack_pop(&mut stack)?;
                    charge_gas(&mut gas_left, GAS_HIGH)?;
                    if !cond.is_zero() {
                        let dest_usize: usize =
                            dest.try_into().map_err(|_| EvmError::InvalidJumpDest)?;
                        if !jumpdests.contains(&dest_usize) {
                            return Err(EvmError::InvalidJumpDest);
                        }
                        pc = dest_usize;
                    } else {
                        pc += 1;
                    }
                }
                0x58 => {
                    // PC
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::from(pc))?;
                    pc += 1;
                }
                0x5A => {
                    // GAS
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::from(gas_left))?;
                    pc += 1;
                }
                0x5B => {
                    // JUMPDEST
                    charge_gas(&mut gas_left, GAS_JUMPDEST)?;
                    pc += 1;
                }

                // ============================================
                // PUSH0..PUSH32
                // ============================================
                0x5F => {
                    // PUSH0
                    charge_gas(&mut gas_left, GAS_BASE)?;
                    stack_push(&mut stack, U256::ZERO)?;
                    pc += 1;
                }
                op @ 0x60..=0x7F => {
                    // PUSH1..PUSH32
                    let num_bytes = (op - 0x5F) as usize;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    let mut bytes = [0u8; 32];
                    let start = pc + 1;
                    for i in 0..num_bytes {
                        let pos = start + i;
                        if pos < code_len {
                            bytes[32 - num_bytes + i] = code[pos];
                        }
                    }
                    stack_push(&mut stack, U256::from_be_bytes(bytes))?;
                    pc += 1 + num_bytes;
                }

                // ============================================
                // DUP1..DUP16
                // ============================================
                op @ 0x80..=0x8F => {
                    let item_number = (op - 0x80) as usize;
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    if item_number >= stack.len() {
                        return Err(EvmError::StackUnderflow);
                    }
                    let val = stack[stack.len() - 1 - item_number];
                    stack_push(&mut stack, val)?;
                    pc += 1;
                }

                // ============================================
                // SWAP1..SWAP16
                // ============================================
                op @ 0x90..=0x9F => {
                    let item_number = (op - 0x8F) as usize; // SWAP1 = swap with 1st below top
                    charge_gas(&mut gas_left, GAS_VERY_LOW)?;
                    if item_number >= stack.len() {
                        return Err(EvmError::StackUnderflow);
                    }
                    let top = stack.len() - 1;
                    stack.swap(top, top - item_number);
                    pc += 1;
                }

                // ============================================
                // Storage: SLOAD, SSTORE, TLOAD, TSTORE
                // ============================================
                0x54 => {
                    // SLOAD
                    let key_u256 = stack_pop(&mut stack)?;
                    let key_bytes = key_u256.to_be_bytes::<32>();
                    let pair = (current_address_bytes, key_bytes);

                    if accessed_storage_keys.contains(&pair) {
                        charge_gas(&mut gas_left, GAS_WARM_ACCESS)?;
                    } else {
                        accessed_storage_keys.insert(pair);
                        charge_gas(&mut gas_left, GAS_COLD_SLOAD)?;
                    }

                    // Call Python for the actual state read
                    if let Some(cb) = state_callback {
                        let result = cb.call1(("sload", key_bytes.as_slice()))?;
                        let val_bytes: Vec<u8> = result.extract()?;
                        let mut buf = [0u8; 32];
                        let start = 32usize.saturating_sub(val_bytes.len());
                        let copy_len = val_bytes.len().min(32);
                        buf[start..start + copy_len].copy_from_slice(&val_bytes[..copy_len]);
                        stack_push(&mut stack, U256::from_be_bytes(buf))?;
                    } else {
                        stack_push(&mut stack, U256::ZERO)?;
                    }
                    pc += 1;
                }
                0x55 => {
                    // SSTORE
                    let key_u256 = stack_pop(&mut stack)?;
                    let new_value = stack_pop(&mut stack)?;
                    let key_bytes = key_u256.to_be_bytes::<32>();

                    if gas_left <= GAS_CALL_STIPEND {
                        return Err(EvmError::OutOfGas);
                    }
                    if is_static {
                        // WriteInStaticContext — fall back to Python
                        // Push values back and fallback
                        stack_push(&mut stack, new_value)?;
                        stack_push(&mut stack, key_u256)?;
                        fallback_op = 0x55;
                        return Ok(());
                    }

                    // Get original and current values from Python
                    if let Some(cb) = state_callback {
                        let info = cb.call1(("sstore_info", key_bytes.as_slice()))?;
                        let (orig_bytes, curr_bytes): (Vec<u8>, Vec<u8>) = info.extract()?;

                        let mut obuf = [0u8; 32];
                        let ostart = 32usize.saturating_sub(orig_bytes.len());
                        obuf[ostart..ostart + orig_bytes.len().min(32)]
                            .copy_from_slice(&orig_bytes[..orig_bytes.len().min(32)]);
                        let original_value = U256::from_be_bytes(obuf);

                        let mut cbuf = [0u8; 32];
                        let cstart = 32usize.saturating_sub(curr_bytes.len());
                        cbuf[cstart..cstart + curr_bytes.len().min(32)]
                            .copy_from_slice(&curr_bytes[..curr_bytes.len().min(32)]);
                        let current_value = U256::from_be_bytes(cbuf);

                        // Gas calculation
                        let mut gas_cost: u64 = 0;
                        let pair = (current_address_bytes, key_bytes);

                        if !accessed_storage_keys.contains(&pair) {
                            accessed_storage_keys.insert(pair);
                            gas_cost += GAS_COLD_SLOAD;
                        }

                        if original_value == current_value && current_value != new_value {
                            if original_value.is_zero() {
                                gas_cost += GAS_STORAGE_SET;
                            } else {
                                gas_cost += GAS_STORAGE_UPDATE - GAS_COLD_SLOAD;
                            }
                        } else {
                            gas_cost += GAS_WARM_ACCESS;
                        }

                        // Refund counter
                        if current_value != new_value {
                            if !original_value.is_zero() && !current_value.is_zero() && new_value.is_zero() {
                                refund_counter += REFUND_STORAGE_CLEAR;
                            }
                            if !original_value.is_zero() && current_value.is_zero() {
                                refund_counter -= REFUND_STORAGE_CLEAR;
                            }
                            if original_value == new_value {
                                if original_value.is_zero() {
                                    refund_counter += (GAS_STORAGE_SET - GAS_WARM_ACCESS) as i64;
                                } else {
                                    refund_counter += (GAS_STORAGE_UPDATE - GAS_COLD_SLOAD - GAS_WARM_ACCESS) as i64;
                                }
                            }
                        }

                        charge_gas(&mut gas_left, gas_cost)?;

                        // Write the value
                        let new_val_bytes = new_value.to_be_bytes::<32>();
                        cb.call1(("sstore_set", key_bytes.as_slice(), new_val_bytes.as_slice()))?;
                    }
                    pc += 1;
                }
                0x5C => {
                    // TLOAD
                    let key_u256 = stack_pop(&mut stack)?;
                    let key_bytes = key_u256.to_be_bytes::<32>();
                    charge_gas(&mut gas_left, GAS_WARM_ACCESS)?;

                    if let Some(cb) = state_callback {
                        let result = cb.call1(("tload", key_bytes.as_slice()))?;
                        let val_bytes: Vec<u8> = result.extract()?;
                        let mut buf = [0u8; 32];
                        let start = 32usize.saturating_sub(val_bytes.len());
                        let copy_len = val_bytes.len().min(32);
                        buf[start..start + copy_len].copy_from_slice(&val_bytes[..copy_len]);
                        stack_push(&mut stack, U256::from_be_bytes(buf))?;
                    } else {
                        stack_push(&mut stack, U256::ZERO)?;
                    }
                    pc += 1;
                }
                0x5D => {
                    // TSTORE
                    let key_u256 = stack_pop(&mut stack)?;
                    let new_value = stack_pop(&mut stack)?;
                    let key_bytes = key_u256.to_be_bytes::<32>();
                    charge_gas(&mut gas_left, GAS_WARM_ACCESS)?;

                    if is_static {
                        stack_push(&mut stack, new_value)?;
                        stack_push(&mut stack, key_u256)?;
                        fallback_op = 0x5D;
                        return Ok(());
                    }

                    if let Some(cb) = state_callback {
                        let new_val_bytes = new_value.to_be_bytes::<32>();
                        cb.call1(("tstore", key_bytes.as_slice(), new_val_bytes.as_slice()))?;
                    }
                    pc += 1;
                }

                // ============================================
                // RETURN
                // ============================================
                0xF3 => {
                    // Fall back to Python — RETURN needs to set evm.output
                    fallback_op = op;
                    return Ok(());
                }

                // ============================================
                // REVERT
                // ============================================
                0xFD => {
                    // Fall back to Python — REVERT needs to set evm.output + error
                    fallback_op = op;
                    return Ok(());
                }

                // ============================================
                // Everything else: fall back to Python
                // ============================================
                _ => {
                    fallback_op = op;
                    return Ok(());
                }
            }
        }
        Ok(())
    })();

    match result {
        Ok(()) => {}
        Err(e) => {
            error = Some(e.to_string());
            gas_left = 0;
            running = false;
        }
    }

    // Convert stack to BE bytes for return
    let stack_out: Vec<[u8; 32]> = stack.iter().map(|v| v.to_be_bytes()).collect();
    let memory_out = PyBytes::new(py, &memory);

    // Convert accessed storage keys to list of (addr, key) byte pairs
    let storage_keys_out: Vec<(&[u8], &[u8])> = Vec::new(); // unused for now

    // Return a tuple: (pc, gas_left, stack_bytes, memory, running, fallback_op, error, refund_counter)
    Ok((
        pc as u64,
        gas_left,
        stack_out,
        memory_out.into_any(),
        running,
        fallback_op,
        error,
        refund_counter,
    )
        .into_pyobject(py)?
        .into())
}

/// Compute valid jump destinations from EVM bytecode.
/// This replaces the Python `get_valid_jump_destinations` function.
#[pyfunction]
fn valid_jump_destinations(code: &[u8]) -> Vec<u64> {
    let mut result = Vec::new();
    let mut pc = 0usize;
    let code_len = code.len();

    while pc < code_len {
        let op = code[pc];
        if op == 0x5B {
            // JUMPDEST
            result.push(pc as u64);
        } else if (0x60..=0x7F).contains(&op) {
            // PUSH1..PUSH32 — skip the data bytes
            let push_data_size = (op - 0x5F) as usize;
            pc += push_data_size;
        }
        pc += 1;
    }
    result
}

/// Python module
#[pymodule]
fn evm_rusty(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(execute_inner_loop, m)?)?;
    m.add_function(wrap_pyfunction!(valid_jump_destinations, m)?)?;
    Ok(())
}
