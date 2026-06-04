#!/usr/bin/env python3

import argparse
import json
import os

import dspy

from module import build_program


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the sample DSPy bundle locally with one question input.",
    )
    parser.add_argument("--question", required=True, help="User issue or support question to send to the agent")
    parser.add_argument(
        "--model",
        default=os.getenv("DSPY_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-4o-mini",
        help="DSPy model name. Defaults to DSPY_MODEL, OPENAI_MODEL, or openai/gpt-4o-mini.",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("DSPY_API_BASE") or os.getenv("OPENAI_API_BASE") or "",
        help="Optional API base URL. Defaults to DSPY_API_BASE or OPENAI_API_BASE.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("DSPY_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
        help="Optional API key. Defaults to DSPY_API_KEY or OPENAI_API_KEY.",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="LM temperature")
    parser.add_argument("--max-tokens", type=int, default=800, help="LM max tokens")
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
    lm = build_lm(args)
    dspy.configure(lm=lm)

    program = build_program()
    prediction = program(question=args.question)
    try:
        payload = prediction.toDict()
    except Exception:
        payload = str(prediction)
    print(json.dumps(payload, indent=2) if isinstance(payload, dict) else payload)


if __name__ == "__main__":
    main()
