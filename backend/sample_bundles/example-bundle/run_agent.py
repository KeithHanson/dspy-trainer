#!/usr/bin/env python3

import argparse
import json

from module import build_program


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the sample DSPy bundle locally with one message input.",
    )
    parser.add_argument("--message", required=True, help="Message to inspect for r/R characters")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    program = build_program()
    prediction = program(message=args.message)
    try:
        payload = prediction.toDict()
    except Exception:
        payload = str(prediction)
    print(json.dumps(payload, indent=2) if isinstance(payload, dict) else payload)


if __name__ == "__main__":
    main()
