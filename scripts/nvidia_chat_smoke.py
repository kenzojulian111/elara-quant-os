from __future__ import annotations

import argparse
import os
from pathlib import Path

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parent.parent


def _seed_env_from_dotenv() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test NVIDIA OpenAI-compatible chat endpoint.")
    parser.add_argument("prompt", nargs="?", default="Jawab singkat: status koneksi NVIDIA API?")
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()

    _seed_env_from_dotenv()
    api_key = os.getenv("NVIDIA_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
    model = os.getenv("OPENAI_MODEL", "z-ai/glm-5.1").strip()
    if not api_key:
        raise SystemExit("NVIDIA_API_KEY kosong.")

    client = OpenAI(base_url=base_url, api_key=api_key)
    request = {
        "model": model,
        "messages": [{"role": "user", "content": args.prompt}],
        "temperature": float(os.getenv("OPENAI_TEMPERATURE", "1") or 1),
        "top_p": float(os.getenv("OPENAI_TOP_P", "1") or 1),
        "max_tokens": int(os.getenv("OPENAI_MAX_TOKENS", "16384") or 16384),
    }
    if args.no_stream:
        completion = client.chat.completions.create(**request)
        print(completion.choices[0].message.content or "")
        return 0

    for chunk in client.chat.completions.create(**request, stream=True):
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        if content:
            print(content, end="", flush=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
