#!/usr/bin/env python3

import argparse
import json
import os

import dspy

from module import build_program


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the event extraction sample bundle locally.")
    parser.add_argument("--email", required=True, help="Email body to parse")
    parser.add_argument("--model", default=os.getenv("DSPY_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-5.4-nano")
    parser.add_argument("--api-base", default=os.getenv("DSPY_API_BASE") or os.getenv("OPENAI_API_BASE") or "")
    parser.add_argument("--api-key", default=os.getenv("DSPY_API_KEY") or os.getenv("OPENAI_API_KEY") or "")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=400)
    return parser.parse_args()


def build_lm(args: argparse.Namespace) -> dspy.LM:
    kwargs = {
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
    }
    if args.api_base:
        kwargs["api_base"] = args.api_base
    if args.api_key:
        kwargs["api_key"] = args.api_key
    return dspy.LM(**kwargs)


def main() -> None:
    args = parse_args()
    dspy.configure(lm=build_lm(args))
    program = build_program()
    prediction = program(email=args.email)
    print(json.dumps(prediction.toDict(), indent=2))


if __name__ == "__main__":
    main()
