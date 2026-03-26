//! Mini-revm: Minimal Rust EVM for the EELS hot path.
//!
//! Architecture:
//!   1. Python calls `run_bytecode(code, gas, stack_data, memory, ...)`
//!   2. Rust owns: pc, gas_left, stack (Vec<U256>), memory (Vec<u8>)
//!   3. For 127/149 opcodes: pure Rust, zero Python calls
//!   4. For 22 opcodes (state, calls, logs): callback to Python
//!   5. On return: write modified state back to Python EVM object
//!
//! LOC estimate: ~800 lines for the minimal version below.

use pyo3::prelude::*;
use ruint::aliases::U256;  // ruint crate: stack-allocated 256-bit int

// ---------------------------------------------------------------
// Core EVM state — all Rust-owned, zero Python objects
// ---------------------------------------------------------------

struct Evm {
    pc: usize,
    gas_left: u64,
    stack: Vec<U256>,
    memory: Vec<u8>,
    code: Vec<u8>,
    running: bool,
    return_data: Vec<u8>,
    refund_counter: i64,
    // Read-only context (extracted from Python at entry)
    valid_jump_destinations: Vec<bool>,  // code_len bitmap
}

// ---------------------------------------------------------------
// Gas constants
// ---------------------------------------------------------------
const GAS_ZERO: u64 = 0;
const GAS_JUMPDEST: u64 = 1;
const GAS_BASE: u64 = 2;
const GAS_VERY_LOW: u64 = 3;
const GAS_LOW: u64 = 5;
const GAS_MID: u64 = 8;
const GAS_HIGH: u64 = 10;
const GAS_MEMORY: u64 = 3;
const GAS_COPY: u64 = 3;

#[inline]
fn charge_gas(evm: &mut Evm, amount: u64) -> Result<(), &'static str> {
    if evm.gas_left < amount {
        return Err("OutOfGasError");
    }
    evm.gas_left -= amount;
    Ok(())
}

// ---------------------------------------------------------------
// Memory helpers
// ---------------------------------------------------------------

#[inline]
fn ceil32(x: u64) -> u64 {
    (x + 31) & !31
}

fn memory_gas_cost(size: u64) -> u64 {
    let words = ceil32(size) / 32;
    words * GAS_MEMORY + (words * words) / 512
}

fn extend_memory(evm: &mut Evm, offset: u64, size: u64) -> Result<u64, &'static str> {
    if size == 0 { return Ok(0); }
    let end = offset + size;
    let current = evm.memory.len() as u64;
    let needed = ceil32(end);
    if needed <= ceil32(current) { return Ok(0); }
    let old_cost = memory_gas_cost(current);
    let new_cost = memory_gas_cost(needed);
    let gas = new_cost - old_cost;
    charge_gas(evm, gas)?;
    evm.memory.resize(needed as usize, 0);
    Ok(gas)
}

// ---------------------------------------------------------------
// Stack helpers
// ---------------------------------------------------------------

#[inline]
fn push(evm: &mut Evm, value: U256) -> Result<(), &'static str> {
    if evm.stack.len() >= 1024 { return Err("StackOverflowError"); }
    evm.stack.push(value);
    Ok(())
}

#[inline]
fn pop(evm: &mut Evm) -> Result<U256, &'static str> {
    evm.stack.pop().ok_or("StackUnderflowError")
}

// ---------------------------------------------------------------
// Opcode implementations — the 127 "pure" opcodes
// ---------------------------------------------------------------

// --- Arithmetic (11 opcodes, ~80 lines) ---

fn op_add(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, a.wrapping_add(b))
}

fn op_mul(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_LOW)?;
    push(evm, a.wrapping_mul(b))
}

fn op_sub(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, a.wrapping_sub(b))
}

fn op_div(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_LOW)?;
    push(evm, if b.is_zero() { U256::ZERO } else { a / b })
}

fn op_mod(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_LOW)?;
    push(evm, if b.is_zero() { U256::ZERO } else { a % b })
}

fn op_addmod(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?; let n = pop(evm)?;
    charge_gas(evm, GAS_MID)?;
    if n.is_zero() {
        push(evm, U256::ZERO)
    } else {
        push(evm, a.add_mod(b, n))
    }
}

fn op_mulmod(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?; let n = pop(evm)?;
    charge_gas(evm, GAS_MID)?;
    if n.is_zero() {
        push(evm, U256::ZERO)
    } else {
        push(evm, a.mul_mod(b, n))
    }
}

// EXP, SDIV, SMOD, SIGNEXTEND: ~40 more lines each
// (omitted for brevity — straightforward U256 operations)

// --- Comparison (6 opcodes, ~30 lines) ---

fn op_lt(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, if a < b { U256::from(1) } else { U256::ZERO })
}

fn op_eq(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, if a == b { U256::from(1) } else { U256::ZERO })
}

fn op_iszero(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, if a.is_zero() { U256::from(1) } else { U256::ZERO })
}

// GT, SLT, SGT: same pattern, ~10 lines each

// --- Bitwise (9 opcodes, ~50 lines) ---

fn op_and(evm: &mut Evm) -> Result<(), &'static str> {
    let a = pop(evm)?; let b = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, a & b)
}

fn op_shl(evm: &mut Evm) -> Result<(), &'static str> {
    let shift = pop(evm)?; let value = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    push(evm, if shift >= U256::from(256) { U256::ZERO } else {
        value << shift.to::<usize>()
    })
}

// OR, XOR, NOT, BYTE, SHR, SAR, CLZ: same pattern

// --- Stack (66 opcodes, but only 4 functions ~40 lines) ---

fn op_pop(evm: &mut Evm) -> Result<(), &'static str> {
    pop(evm)?;
    charge_gas(evm, GAS_BASE)?;
    Ok(())
}

fn op_push_n(evm: &mut Evm, n: usize) -> Result<(), &'static str> {
    charge_gas(evm, if n == 0 { GAS_BASE } else { GAS_VERY_LOW })?;
    if n == 0 {
        push(evm, U256::ZERO)?;
    } else {
        let start = evm.pc + 1;
        let mut bytes = [0u8; 32];
        let code_len = evm.code.len();
        for i in 0..n {
            if start + i < code_len {
                bytes[32 - n + i] = evm.code[start + i];
            }
        }
        push(evm, U256::from_be_bytes(bytes))?;
    }
    evm.pc += n; // caller adds 1
    Ok(())
}

fn op_dup_n(evm: &mut Evm, n: usize) -> Result<(), &'static str> {
    charge_gas(evm, GAS_VERY_LOW)?;
    let len = evm.stack.len();
    if n >= len { return Err("StackUnderflowError"); }
    let val = evm.stack[len - 1 - n];
    push(evm, val)
}

fn op_swap_n(evm: &mut Evm, n: usize) -> Result<(), &'static str> {
    charge_gas(evm, GAS_VERY_LOW)?;
    let len = evm.stack.len();
    if n >= len { return Err("StackUnderflowError"); }
    evm.stack.swap(len - 1, len - 1 - n);
    Ok(())
}

// --- Memory (5 opcodes, ~60 lines) ---

fn op_mload(evm: &mut Evm) -> Result<(), &'static str> {
    let offset = pop(evm)?.to::<u64>();
    charge_gas(evm, GAS_VERY_LOW)?;
    extend_memory(evm, offset, 32)?;
    let o = offset as usize;
    let val = U256::from_be_slice(&evm.memory[o..o + 32]);
    push(evm, val)
}

fn op_mstore(evm: &mut Evm) -> Result<(), &'static str> {
    let offset = pop(evm)?.to::<u64>();
    let value = pop(evm)?;
    charge_gas(evm, GAS_VERY_LOW)?;
    extend_memory(evm, offset, 32)?;
    let o = offset as usize;
    evm.memory[o..o + 32].copy_from_slice(&value.to_be_bytes::<32>());
    Ok(())
}

// MSTORE8, MSIZE, MCOPY: ~30 more lines

// --- Control flow (6 opcodes, ~50 lines) ---

fn op_stop(evm: &mut Evm) -> Result<(), &'static str> {
    evm.running = false;
    Ok(())
}

fn op_jump(evm: &mut Evm) -> Result<(), &'static str> {
    let dest = pop(evm)?.to::<usize>();
    charge_gas(evm, GAS_MID)?;
    if dest >= evm.code.len() || !evm.valid_jump_destinations[dest] {
        return Err("InvalidJumpDestError");
    }
    evm.pc = dest;
    // Return special value to skip pc += 1
    Ok(()) // caller checks if pc was modified
}

fn op_jumpdest(evm: &mut Evm) -> Result<(), &'static str> {
    charge_gas(evm, GAS_JUMPDEST)?;
    Ok(())
}

// JUMPI, PC, GAS: ~20 more lines

// --- Codecopy (~20 lines) ---

fn op_codecopy(evm: &mut Evm) -> Result<(), &'static str> {
    let mem_offset = pop(evm)?.to::<u64>();
    let code_offset = pop(evm)?.to::<u64>();
    let size = pop(evm)?.to::<u64>();

    let words = ceil32(size) / 32;
    charge_gas(evm, GAS_VERY_LOW + GAS_COPY * words)?;
    extend_memory(evm, mem_offset, size)?;

    let code_len = evm.code.len() as u64;
    for i in 0..size {
        let byte = if code_offset + i < code_len {
            evm.code[(code_offset + i) as usize]
        } else {
            0
        };
        evm.memory[(mem_offset + i) as usize] = byte;
    }
    Ok(())
}

// ---------------------------------------------------------------
// The interpreter loop — ~50 lines
// ---------------------------------------------------------------

fn execute(
    evm: &mut Evm,
    // Callback for the 22 opcodes that need Python
    py_callback: &dyn Fn(u8, &mut Evm) -> Result<(), String>,
) -> Result<(), String> {
    while evm.running && evm.pc < evm.code.len() {
        let opcode = evm.code[evm.pc];
        let old_pc = evm.pc;

        let result = match opcode {
            0x00 => op_stop(evm),
            0x01 => op_add(evm),
            0x02 => op_mul(evm),
            0x03 => op_sub(evm),
            0x04 => op_div(evm),
            0x06 => op_mod(evm),
            0x08 => op_addmod(evm),
            0x09 => op_mulmod(evm),
            0x10 => op_lt(evm),
            0x14 => op_eq(evm),
            0x15 => op_iszero(evm),
            0x16 => op_and(evm),
            0x1B => op_shl(evm),
            0x39 => op_codecopy(evm),
            0x50 => op_pop(evm),
            0x51 => op_mload(evm),
            0x52 => op_mstore(evm),
            0x56 => op_jump(evm),
            0x5B => op_jumpdest(evm),
            // PUSH0..PUSH32
            op @ 0x5F..=0x7F => op_push_n(evm, (op - 0x5F) as usize),
            // DUP1..DUP16
            op @ 0x80..=0x8F => op_dup_n(evm, (op - 0x80) as usize),
            // SWAP1..SWAP16
            op @ 0x90..=0x9F => op_swap_n(evm, (op - 0x90 + 1) as usize),

            // Everything else: call back to Python
            _ => {
                py_callback(opcode, evm)?;
                Ok(())
            }
        };

        result.map_err(|e| e.to_string())?;

        // Advance pc (unless JUMP/JUMPI already set it)
        if evm.pc == old_pc {
            evm.pc += 1;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------
// PyO3 bridge — ~100 lines
// ---------------------------------------------------------------
//
// #[pyfunction]
// fn run_bytecode(
//     py: Python<'_>,
//     evm_obj: &Bound<'_, PyAny>,      // Python EVM object
//     py_dispatch: &Bound<'_, PyAny>,   // Python fallback for state ops
// ) -> PyResult<()> {
//     // 1. Extract state from Python → Rust
//     let code: Vec<u8> = evm_obj.getattr("code")?.extract()?;
//     let gas_left: u64 = evm_obj.getattr("gas_left")?.extract()?;
//     let pc: usize = evm_obj.getattr("pc")?.extract()?;
//     // ... extract stack, memory, valid_jump_destinations
//
//     let mut evm = Evm { code, gas_left, pc, ... };
//
//     // 2. Run in pure Rust
//     let py_callback = |opcode: u8, evm: &mut Evm| -> Result<(), String> {
//         // Write current Rust state back to Python
//         // Call Python opcode implementation
//         // Read modified state back from Python
//     };
//     execute(&mut evm, &py_callback)?;
//
//     // 3. Write results back to Python
//     evm_obj.setattr("gas_left", Uint(evm.gas_left))?;
//     evm_obj.setattr("pc", Uint(evm.pc))?;
//     // ... write stack, memory back
//
//     Ok(())
// }

// ---------------------------------------------------------------
// LOC summary:
//
//   Core types + helpers:     ~80 lines
//   Arithmetic (11 ops):     ~120 lines
//   Comparison (6 ops):       ~40 lines
//   Bitwise (9 ops):          ~60 lines
//   Stack (4 functions):      ~50 lines
//   Memory (5 ops):           ~70 lines
//   Control flow (6 ops):     ~50 lines
//   Environment reads (12):   ~80 lines  (extract from context struct)
//   Block reads (11):         ~60 lines  (extract from context struct)
//   Codecopy + calldatacopy:  ~40 lines
//   Keccak:                   ~10 lines  (call tiny_keccak crate)
//   Interpreter loop:         ~60 lines
//   PyO3 bridge:             ~120 lines
//   ─────────────────────────────────
//   Total:                   ~840 lines
//
// Dependencies: ruint, tiny-keccak, pyo3
//
// What you get:
//   - 127/149 opcodes run in pure Rust with ZERO Python interaction
//   - Stack is Vec<U256> — no Python object per element
//   - Memory is Vec<u8> — no Python bytearray overhead
//   - Gas is u64 — no Uint object creation
//   - PC is usize — no Uint object creation
//   - For the 22 state/call opcodes: sync Rust↔Python, call Python,
//     sync back. Costs ~5us per state op (vs ~0.01us for pure Rust ops)
//
// Expected speedup: ~10-20x on compute-heavy benchmarks,
//                   ~3-5x on state-heavy code (SLOAD/SSTORE dominant)
// ---------------------------------------------------------------
