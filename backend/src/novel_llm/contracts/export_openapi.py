"""Export the OpenAPI contract for typed client generation (ADR-0008).

Usage: uv run python -m novel_llm.contracts.export_openapi --output ../frontend/openapi.json
The generated client and this export are committed; `make check` verifies they
have not drifted from the code.
"""

import argparse
import json
from pathlib import Path

from novel_llm.api.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="frontend/openapi.json", help="output file path")
    args = parser.parse_args()

    spec = create_app().openapi()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
