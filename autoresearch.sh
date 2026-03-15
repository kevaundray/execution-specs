#!/usr/bin/env bash
set -euo pipefail

# Benchmark wrapper for tox -e py3. Emits METRIC tox_py3_seconds=<seconds>.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "${SCRIPT_DIR}"

command=(uvx tox -e py3 "$@")

start=$(python - <<'PY'
import time
print(time.perf_counter())
PY
)

"${command[@]}"

end=$(python - <<'PY'
import time
print(time.perf_counter())
PY
)

duration=$(python - <<'PY'
import sys
start = float(sys.argv[1])
end = float(sys.argv[2])
print(f"{end - start:.3f}")
PY
"${start}" "${end}")

echo "METRIC tox_py3_seconds=${duration}"
