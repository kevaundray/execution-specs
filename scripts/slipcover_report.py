"""Post-process slipcover JSON output into XML and enforce coverage gate."""
import json
import sys
from pathlib import Path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--xml", type=Path, default=None)
    parser.add_argument("--fail-under", type=float, default=0)
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    # Generate XML if requested
    if args.xml is not None:
        from slipcover.slipcover import Slipcover

        sc = Slipcover(source=["src/ethereum"])
        # Load the data into a Slipcover-compatible format
        from slipcover import print_xml

        with open(args.xml, "w") as out:
            print_xml(
                data,
                source_paths=["src/ethereum"],
                with_branches=True,
                outfile=out,
            )
        print(f"XML report: {args.xml}")

    # Calculate and print summary
    total_lines = 0
    covered_lines = 0
    for info in data["files"].values():
        total_lines += len(info.get("executed_lines", []))
        total_lines += len(info.get("missing_lines", []))
        covered_lines += len(info.get("executed_lines", []))

    pct = covered_lines / total_lines * 100 if total_lines else 0
    print(f"Coverage: {covered_lines}/{total_lines} = {pct:.1f}%")

    if args.fail_under > 0 and pct < args.fail_under:
        print(
            f"FAIL: Coverage {pct:.1f}% < {args.fail_under}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
