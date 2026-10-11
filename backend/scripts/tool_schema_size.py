"""Size of the always-on (core) tool schemas as sent to the model. Run from backend/:
PYTHONPATH=. .venv/bin/python scripts/tool_schema_size.py"""
import json
import os
import tempfile

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolsize-"))
from personal_os import app as appmod, tools  # noqa: E402

tb = appmod.toolbox
modes = {n: "on" for n, s in tb.specs.items() if tools.is_core(s) or n == "tool_search"}
built = tb.schemas(modes)
total = len(json.dumps(built))
print(f"total chars {total}  tools {len(built)}  est tokens {total // 4}")
for n, name in sorted(((len(json.dumps(t)), t["function"]["name"]) for t in built), reverse=True):
    print(f"{n:6d} {name}")
