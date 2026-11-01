"""
title: Experiment Probe Tool
author: chumba experiment
version: 0.2.0
required_open_webui_version: 0.11.4
"""


class Tools:
    def __init__(self):
        pass

    def probe(self, marker: str = "A") -> str:
        """Return a small deterministic result for tool-loop testing."""
        return f"PROBE_OK:{marker}"

    def probe_fail(self, marker: str = "FAIL") -> str:
        """Deliberately raise to produce a deterministic tool error."""
        raise RuntimeError(f"INTENTIONAL_TOOL_ERROR:{marker}")

    def large_probe(self, marker: str = "probe") -> str:
        """Return a large synthetic result used to grow the tool-loop context."""
        return "TOOL_RESULT_BEGIN " + marker + " " + ("X" * 12000) + " TOOL_RESULT_END"

    def huge_probe(self, marker: str = "huge") -> str:
        """Return a result large enough to exceed a 32K prompt budget."""
        return "TOOL_RESULT_BEGIN " + marker + " " + ("слово " * 30000) + " TOOL_RESULT_END"
