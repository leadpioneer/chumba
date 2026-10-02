#!/usr/bin/env python3
"""Батч-чтение URL через search-mcp web_url_read: печатает URL -> символов (или ERROR)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env, mcp_call  # noqa: E402

URLS = [
    "https://example.com",
    "https://github.com/ggml-org/llama.cpp",
    "https://en.wikipedia.org/wiki/Llama.cpp",
    "https://nowsecure.nl/",
    "https://www.reddit.com/r/LocalLLaMA/",
    "https://habr.com/ru/",
]


def main():
    cfg = load_env()
    token = cfg.get("SEARCH_MCP_TOKEN", "")
    for url in URLS:
        try:
            text = mcp_call("http://127.0.0.1:8085/mcp", token, "web_url_read", {"url": url})
            print(f"{len(text):7d}  {url}")
        except SystemExit as exc:
            print(f"  ERROR  {url}  {str(exc)[:140]}")


if __name__ == "__main__":
    main()
