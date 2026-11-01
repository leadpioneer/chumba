"""
title: Experiment Large Tool
author: chumba experiment
version: 0.1.0
required_open_webui_version: 0.11.4
"""


class Tools:
    def __init__(self):
        pass

    def large_probe(self, marker: str = "probe") -> str:
        """Return a large synthetic result used to grow the tool-loop context."""
        size = 12000
        return "TOOL_RESULT_BEGIN " + marker + " " + ("X" * size) + " TOOL_RESULT_END"
