# Autoresearch: tox -e py3 duration

## Objective
Speed up the `tox -e py3` environment (fill + pytest) while keeping
full test fidelity and correctness.

## Metrics
- **Primary**: tox_py3_seconds (s, lower is better)
- **Secondary**: none yet (add if we start tracking memory/log size)

## How to Run
`./autoresearch.sh` — runs `uvx tox -e py3` and prints `METRIC
tox_py3_seconds=<seconds>`.

## Files in Scope
- `tox.ini` — tox env configuration, xdist settings, coverage config paths.
- `pyproject.toml` — project/tooling config (pytest, coverage, ruff, mypy),
  may impact fill/pytest behavior.
- `scripts/` — helper scripts used by tests or tooling if tweaks help runtime.
- `packages/testing/` — fill/pytest plugin implementations and helpers.
- `tests/` — fixtures and test helpers; safe to optimize utilities but do not
  skip or weaken coverage.

## Off Limits
- Do not skip, delete, or mark tests as xfail/skip to win the benchmark.
- Do not change expected outputs/fixtures to mask failures.
- Avoid vendorized/third-party content under `vendor/`.

## Constraints
- Tests must pass (`tox -e py3` must exit 0).
- No new runtime dependencies unless justified and lightweight.
- Keep changes readable and in line with project style (79-char lines,
  mypy/ruff expectations).

## What's Been Tried
- Baseline: 805.655s via `uvx tox -e py3`. T8n cache hit rate 100%;
  coverage emits module-not-measured warnings.
- Changed `py3` fill to `-n auto` with `--maxprocesses 8` default (env
  overrides). Runtime improved to 674.667s.
- Raised `py3` default cap to `--maxprocesses 12`; runtime now 540.056s.
- Raised cap to `--maxprocesses 16`; runtime now 504.597s.
