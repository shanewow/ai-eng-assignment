"""Smoke-test the OPENAI_API_KEY in .env with one tiny call to the cheapest model.

Usage (from repo root):  uv run python scripts/check_openai_key.py [model]
"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not set — put it in .env at the repo root")

model = sys.argv[1] if len(sys.argv) > 1 else "gpt-5-nano"

# gpt-5 family are reasoning models: they take max_completion_tokens (not
# max_tokens) and reject non-default temperature.
response = OpenAI().chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Reply with the single word: ok"}],
    max_completion_tokens=50,
    reasoning_effort="minimal",
)
print("model:", response.model)
print("response:", response.choices[0].message.content)
print("usage:", response.usage.prompt_tokens, "in /", response.usage.completion_tokens, "out")
print("key works")
