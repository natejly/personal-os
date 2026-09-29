import argparse
import os

import uvicorn


def load_dotenv() -> None:
    """Load ../.env (repo root) if present; existing env vars win; ${VAR} references expand."""
    import re
    from pathlib import Path

    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not env_path.exists():
            continue
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            v = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
            os.environ.setdefault(k, v)
        break


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Personal OS backend")
    p.add_argument("--port", type=int, default=int(os.environ.get("PERSONAL_OS_PORT", "8765")))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--data-dir", default=os.environ.get("PERSONAL_OS_DATA_DIR", "./data"))
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    os.environ["PERSONAL_OS_DATA_DIR"] = args.data_dir
    uvicorn.run("personal_os.app:app", host=args.host, port=args.port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
