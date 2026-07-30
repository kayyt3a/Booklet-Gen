#!/usr/bin/env python3
"""Probe which Gemini models this API key can actually call.

The models endpoint lists models the account can *see*, which is not the same
as the models it can *use*: a listed model can still return 404 "no longer
available to new users" on the first real request. This sends one tiny prompt
to each candidate so a model is proven working before it goes in .env.

Usage:
    python scripts/check_models.py                 # probe the default candidates
    python scripts/check_models.py gemini-3.6-flash gemini-pro-latest
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

warnings.filterwarnings("ignore")

# Newest first. Preview models are flagged: they can be withdrawn without
# notice, which matters for a product you are selling from.
CANDIDATES = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-pro-latest",
    "gemini-2.5-flash",
]

PROMPT = "Reply with the single word: ok"


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set. Add it to .env first.")
        return 1

    import google.generativeai as genai
    genai.configure(api_key=key)

    names = sys.argv[1:] or CANDIDATES
    print(f"Probing {len(names)} models with one short prompt each.\n")
    working = []
    for name in names:
        label = name + ("  (preview)" if "preview" in name else "")
        try:
            reply = genai.GenerativeModel(name).generate_content(
                PROMPT, request_options={"timeout": 20})
            text = (reply.text or "").strip().replace("\n", " ")[:40]
            print(f"  OK      {label:34} -> {text!r}")
            working.append(name)
        except Exception as e:
            reason = str(e).split("\n")[0][:90]
            print(f"  FAILED  {label:34} -> {reason}")

    print()
    if working:
        print("Usable as GEMINI_MODEL_STRONG:")
        for n in working:
            print(f"  {n}")
        print("\nPrefer a non-preview model for work you are selling: preview")
        print("models can be withdrawn without notice.")
    else:
        print("Nothing responded. Check the API key and that billing is active.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
