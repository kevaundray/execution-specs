"""Thin wrapper to invoke fill, suitable for slipcover instrumentation.

slipcover consumes -m as its own flag (run module), which conflicts
with pytest's -m (marker expression). This wrapper injects the marker
filter before passing args to fill.
"""
import sys

# Insert marker filter at position 1 (before other args) if not present
if not any(a == "-m" for a in sys.argv[1:]):
    sys.argv.insert(1, "-m")
    sys.argv.insert(2, "not slow and not benchmark")

from execution_testing.cli.pytest_commands.fill import fill

if __name__ == "__main__":
    fill()
