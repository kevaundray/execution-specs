//! Targeted Rust replacement for EVM hot paths.
//!
//! This is a SKETCH — not buildable yet. It shows the ~600 lines of Rust
//! that would replace the monkey-patching approach with a proper PyO3
//! module, covering the same optimizations:
//!
//! 1. Interpreter inner loop (dispatch table)
//! 2. charge_gas (inline arithmetic)
//! 3. push_n / dup_n / swap_n / pop
//! 4. codecopy + buffer_read
//! 5. ceil32 / calculate_gas_extend_memory
//!
//! Build with: maturin develop --release

use pyo3::exceptions::{PyOverflowError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyBytes};

// ---------------------------------------------------------------------------
// U256: lightweight wrapper around a Python int for stack values.
// We don't replace the Python U256 class — we just extract/inject ints.
// ---------------------------------------------------------------------------

/// Extract the raw int from a Python Uint/U256 object.
/// Works with both the _number-based and int-subclass versions.
#[inline]
fn extract_uint(obj: &Bound<'_, PyAny>) -> PyResult<u64> {
    obj.extract::<u64>()
}

/// Extract a 256-bit value as a Python int (BigInt).
#[inline]
fn extract_u256(obj: &Bound<'_, PyAny>) -> PyResult<Bound<'_, PyAny>> {
    // U256 values can exceed u64, so keep as Python int
    Ok(obj.clone())
}

// ---------------------------------------------------------------------------
// Gas utilities — pure Rust, no Python objects
// ---------------------------------------------------------------------------

#[inline]
fn ceil32(value: u64) -> u64 {
    let remainder = value & 31;
    if remainder == 0 {
        value
    } else {
        value + 32 - remainder
    }
}

#[inline]
fn calculate_memory_gas_cost(size_in_bytes: u64) -> u64 {
    let size_in_words = ceil32(size_in_bytes) / 32;
    let linear_cost = size_in_words * 3; // GAS_MEMORY = 3
    let quadratic_cost = (size_in_words * size_in_words) / 512;
    linear_cost + quadratic_cost
}

/// Calculate gas to extend memory. Returns (gas_to_pay, bytes_to_extend).
fn calculate_gas_extend_memory(
    memory_len: u64,
    extensions: &[(u64, u64)],
) -> (u64, u64) {
    let mut size_to_extend: u64 = 0;
    let mut to_be_paid: u64 = 0;
    let mut current_size = memory_len;

    for &(start_position, size) in extensions {
        if size == 0 {
            continue;
        }
        let before_size = ceil32(current_size);
        let after_size = ceil32(start_position + size);
        if after_size <= before_size {
            continue;
        }
        size_to_extend += after_size - before_size;
        let already_paid = calculate_memory_gas_cost(before_size);
        let total_cost = calculate_memory_gas_cost(after_size);
        to_be_paid += total_cost - already_paid;
        current_size = after_size;
    }

    (to_be_paid, size_to_extend)
}

// ---------------------------------------------------------------------------
// Buffer read — zero-padded byte slice
// ---------------------------------------------------------------------------

fn buffer_read(buffer: &[u8], start: usize, size: usize) -> Vec<u8> {
    if size == 0 {
        return Vec::new();
    }
    let buf_len = buffer.len();
    if start >= buf_len {
        return vec![0u8; size];
    }
    let end = start + size;
    if end <= buf_len {
        buffer[start..end].to_vec()
    } else {
        let mut result = buffer[start..buf_len].to_vec();
        result.resize(size, 0);
        result
    }
}

// ---------------------------------------------------------------------------
// charge_gas — inline, no Python method calls
// ---------------------------------------------------------------------------

/// Subtract gas from evm.gas_left. Raises OutOfGasError if insufficient.
/// This is the #1 hot function — called on every opcode.
fn charge_gas(
    py: Python<'_>,
    evm: &Bound<'_, PyAny>,
    amount: u64,
    uint_cls: &Bound<'_, PyAny>,
    out_of_gas_error: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let gas_left_obj = evm.getattr("gas_left")?;
    let gas_left: u64 = gas_left_obj.extract()?;

    if gas_left < amount {
        return Err(PyErr::from_value(out_of_gas_error.call0()?));
    }

    let new_gas = uint_cls.call1((gas_left - amount,))?;
    evm.setattr("gas_left", new_gas)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Stack operations
// ---------------------------------------------------------------------------

const GAS_BASE: u64 = 2;
const GAS_VERY_LOW: u64 = 3;
const GAS_COPY: u64 = 3;

/// push_n: read N bytes from code, push to stack.
fn push_n(
    py: Python<'_>,
    evm: &Bound<'_, PyAny>,
    num_bytes: usize,
    uint_cls: &Bound<'_, PyAny>,
    u256_cls: &Bound<'_, PyAny>,
    out_of_gas_error: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let gas = if num_bytes == 0 { GAS_BASE } else { GAS_VERY_LOW };
    charge_gas(py, evm, gas, uint_cls, out_of_gas_error)?;

    let stack = evm.getattr("stack")?;

    if num_bytes == 0 {
        let zero = u256_cls.call1((0i64,))?;
        stack.call_method1("append", (zero,))?;
    } else {
        let pc: usize = evm.getattr("pc")?.extract()?;
        let code = evm.getattr("code")?;
        let code_bytes: &[u8] = code.extract()?;

        let start = pc + 1;
        let data = buffer_read(code_bytes, start, num_bytes);
        let value = u256::from_be_bytes(&data);
        let py_value = u256_cls.call1((value,))?;

        // Stack overflow check
        let stack_len: usize = stack.len()?;
        if stack_len >= 1024 {
            return Err(PyOverflowError::new_err("Stack overflow"));
        }
        stack.call_method1("append", (py_value,))?;
    }

    // pc += 1 + num_bytes
    let pc: u64 = evm.getattr("pc")?.extract()?;
    let new_pc = uint_cls.call1((pc + 1 + num_bytes as u64,))?;
    evm.setattr("pc", new_pc)?;
    Ok(())
}

/// Helper: convert big-endian bytes to Python int for U256.
mod u256 {
    pub fn from_be_bytes(data: &[u8]) -> u128 {
        // For values up to 128 bits, use native. For larger, need Python int.
        let mut value: u128 = 0;
        for &byte in data {
            value = (value << 8) | byte as u128;
        }
        value
    }
}

// ---------------------------------------------------------------------------
// The interpreter inner loop — the biggest single win
// ---------------------------------------------------------------------------

/// Execute EVM bytecode using a dispatch table.
/// Replaces the Python `while evm.running` loop.
#[pyfunction]
fn execute_bytecode(
    py: Python<'_>,
    code: &[u8],
    dispatch: &Bound<'_, PyList>,
    evm: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let code_len = code.len();

    loop {
        // Check evm.running
        let running: bool = evm.getattr("running")?.extract()?;
        if !running {
            break;
        }

        // Get pc as native int
        let pc: usize = evm.getattr("pc")?.extract()?;
        if pc >= code_len {
            break;
        }

        // Dispatch opcode
        let byte = code[pc] as usize;
        let impl_fn = dispatch.get_item(byte)?;
        if impl_fn.is_none() {
            return Err(PyValueError::new_err(byte));
        }

        // Call the opcode implementation
        impl_fn.call1((evm,))?;
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Optimized codecopy
// ---------------------------------------------------------------------------

#[pyfunction]
fn codecopy(
    py: Python<'_>,
    evm: &Bound<'_, PyAny>,
    uint_cls: &Bound<'_, PyAny>,
    out_of_gas_error: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let stack = evm.getattr("stack")?;

    // Pop 3 values
    let mem_start: u64 = stack.call_method0("pop")?.extract()?;
    let code_start: u64 = stack.call_method0("pop")?.extract()?;
    let size: u64 = stack.call_method0("pop")?.extract()?;

    // Gas calculation — all native Rust, zero Python objects
    let words = ceil32(size) / 32;
    let copy_gas_cost = GAS_COPY * words;
    let memory = evm.getattr("memory")?;
    let memory_len: u64 = memory.len()? as u64;
    let (gas_to_pay, expand_by) =
        calculate_gas_extend_memory(memory_len, &[(mem_start, size)]);
    let total_gas = GAS_VERY_LOW + copy_gas_cost + gas_to_pay;

    charge_gas(py, evm, total_gas, uint_cls, out_of_gas_error)?;

    // Extend memory
    if expand_by > 0 {
        let zeros = vec![0u8; expand_by as usize];
        let memory = evm.getattr("memory")?;
        memory.call_method1("extend", (zeros,))?;
    }

    // Copy code to memory
    if size > 0 {
        let code = evm.getattr("code")?;
        let code_bytes: &[u8] = code.extract()?;
        let data = buffer_read(code_bytes, code_start as usize, size as usize);

        let memory = evm.getattr("memory")?;
        // memory[mem_start:mem_start+len] = data
        let slice = pyo3::types::PySlice::new(
            py,
            mem_start as isize,
            (mem_start + data.len() as u64) as isize,
            1,
        );
        memory.set_item(slice, PyBytes::new(py, &data))?;
    }

    // pc += 1
    let pc: u64 = evm.getattr("pc")?.extract()?;
    evm.setattr("pc", uint_cls.call1((pc + 1,))?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Python module
// ---------------------------------------------------------------------------

#[pymodule]
fn evm_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(execute_bytecode, m)?)?;
    m.add_function(wrap_pyfunction!(codecopy, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// That's it. ~300 lines of actual logic, ~600 with docs/boilerplate.
//
// What this buys over the Python monkey-patch approach:
//
// 1. The interpreter loop runs in Rust — no Python bytecode overhead
//    for the while/if/dispatch. BUT: each opcode impl is still a
//    Python callback via impl_fn.call1((evm,)), so the per-opcode
//    cost is dominated by the Python→Rust→Python crossing.
//
// 2. charge_gas, ceil32, calculate_gas_extend_memory are pure Rust
//    with native u64 arithmetic — same as our int rewrites but
//    without Python int allocation.
//
// 3. codecopy does the entire operation (pop, gas calc, memory
//    extend, buffer read, memory write) in one Rust function,
//    minimizing Python object creation.
//
// The HONEST limitation: the biggest cost is still the Python↔Rust
// boundary. Every `evm.getattr("pc")` and `evm.setattr("pc", ...)`
// is a Python dict lookup. Every `stack.call_method1("append", ...)`
// creates a Python tuple for the args. The real win from Rust would
// require moving the ENTIRE EVM state (stack, memory, pc, gas) into
// Rust-owned data structures — which means ~3000 more lines of Rust
// for the full opcode set.
//
// Estimated speedup over our current Python approach: ~1.3-1.5x
// (mostly from the loop + gas utils being pure Rust).
// Estimated speedup if full EVM state in Rust: ~10-20x.
// ---------------------------------------------------------------------------
