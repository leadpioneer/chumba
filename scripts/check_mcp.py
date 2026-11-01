#!/usr/bin/env python3
"""Мини-клиент MCP Streamable HTTP (только stdlib) для проверок стека chumba.

Использование:
  MCP_TOKEN=... python check_mcp.py <url> list
  MCP_TOKEN=... python check_mcp.py <url> call <tool> '<json-args>'
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import mcp_call, mcp_list_tools  # noqa: E402


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    url, mode = sys.argv[1], sys.argv[2]
    token = os.environ.get("MCP_TOKEN", "")

    if mode == "list":
        tools = mcp_list_tools(url, token)
        print("tools:", ", ".join(t["name"] for t in tools))
        for tool in tools:
            desc = (tool.get("description") or "").split("\n")[0][:120]
            print("-", tool["name"], "::", desc)
    elif mode == "call":
        if len(sys.argv) < 4:
            raise SystemExit(__doc__)
        tool = sys.argv[3]
        raw_args = sys.argv[4] if len(sys.argv) > 4 else "{}"
        if raw_args.startswith("@"):
            with open(raw_args[1:], "r", encoding="utf-8") as fh:
                args = json.load(fh)
        else:
            args = json.loads(raw_args)
        print(mcp_call(url, token, tool, args, fail_on_error=True)[:4000])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
