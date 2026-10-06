"""app.py binds `graph_recall` to a GraphRecall instance: every call made on that name must be a method of the class.

A call to the graph_recall *module's* function through that name (e.g. `graph_recall.subgraph`) raised
AttributeError on every chat turn and silently dropped graph retrieval to mention-only matching.
"""
from __future__ import annotations

import ast
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.graph_recall import GraphRecall  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "personal_os" / "app.py"


def test_every_graph_recall_call_in_app_is_a_method() -> None:
    tree = ast.parse(APP.read_text())
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "graph_recall"}
    assert called, "expected app.py to use the graph_recall instance"
    missing = sorted(a for a in called if not hasattr(GraphRecall, a))
    assert not missing, f"not GraphRecall methods: {missing}"
