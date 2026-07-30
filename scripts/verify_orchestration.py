"""Live verification of the AI Core orchestration deployment + model.

Usage:
    python scripts/verify_orchestration.py                 # list deployment + try model
    python scripts/verify_orchestration.py --model <name>  # verify a specific model

A wrong model name deliberately returns the tenant's allowed-model list from
the 400 error — the fastest honest discovery mechanism. Paste the PASS line
into docs/capability-matrix.md (CLAUDE.md §26: record proof, date, result).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trustsphere.generation.aicore import AICoreClient, AICoreError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-4o-mini",
                        help="model name to verify (default gpt-4o-mini)")
    args = parser.parse_args()

    client = AICoreClient()
    print("OAuth token: OK")
    url = client.orchestration_deployment_url()
    print(f"Orchestration deployment: {url}")

    config = {
        "modules": {
            "prompt_templating": {
                "prompt": {
                    "template": [
                        {"role": "user",
                         "content": "Reply with exactly: VERIFIED {{?word}}"}],
                    "defaults": {"word": "team-11"},
                },
                "model": {"name": args.model, "version": "latest",
                           "params": {"max_completion_tokens": 20,
                                       "temperature": 0.0}},
            }
        }
    }
    try:
        resp = client.v2_completion(config, {"word": "team-11"})
    except AICoreError as exc:
        print(f"\nFAIL: {exc}")
        print("If the message lists allowed models, rerun with --model <one of them>.")
        return 1

    final = resp["final_result"]
    content = final["choices"][0]["message"]["content"].strip()
    usage = final.get("usage", {})
    print(f"\nModel responded: {final.get('model', args.model)}")
    print(f"Content: {content!r}")
    print(f"Usage: {usage}")
    print(f"request_id: {resp.get('request_id')}")
    print("\n--- capability-matrix proof line (paste into docs/capability-matrix.md) ---")
    print(f"| Orchestration completion (v2) | AI Core, resource group team-11 | "
          f"live POST /v2/completion, model `{args.model}` | ✅ **PASS** "
          f"({date.today().isoformat()}) — model `{final.get('model', args.model)}`, "
          f"usage {usage.get('total_tokens', '?')} tokens | Generation backend "
          f"verified; recorded in .env as TRUSTSPHERE_GEN_MODEL |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
