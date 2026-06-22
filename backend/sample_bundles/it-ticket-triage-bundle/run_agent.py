#!/usr/bin/env python3

import argparse
import json
import os

import dspy

from module import build_program


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the IT ticket triage sample bundle locally.")
    parser.add_argument("--ticket-title", required=True, help="Ticket title")
    parser.add_argument("--ticket-body", required=True, help="Ticket body")
    parser.add_argument("--model", default=os.getenv("DSPY_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-4o-mini")
    parser.add_argument("--api-base", default=os.getenv("DSPY_API_BASE") or os.getenv("OPENAI_API_BASE") or "")
    parser.add_argument("--api-key", default=os.getenv("DSPY_API_KEY") or os.getenv("OPENAI_API_KEY") or "")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=800)
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
    prediction = program(ticket_title=args.ticket_title, ticket_body=args.ticket_body)
    print(json.dumps(prediction.toDict(), indent=2))


if __name__ == "__main__":
    main()
