import argparse
import os

import uvicorn


def load_dotenv() -> None:
    """Load ../.env (repo root) if present; existing env vars win; ${VAR} references expand."""
    import re
    from pathlib import Path

    if os.environ.get("PERSONAL_OS_PACKAGED"):
        return

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


def watch_parent() -> None:
    """Exit when the launching process dies, so a force-quit or crash cannot orphan the backend."""
    import threading
    import time

    parent = os.getppid()

    def loop() -> None:
        while True:
            time.sleep(1.5)
            if os.getppid() != parent:
                os._exit(0)

    threading.Thread(target=loop, name="parent-watchdog", daemon=True).start()


def main() -> None:
    load_dotenv()
    if os.environ.get("PERSONAL_OS_PARENT_WATCH") == "1":
        watch_parent()
    p = argparse.ArgumentParser(description="Grain backend")
    p.add_argument("--port", type=int, default=int(os.environ.get("PERSONAL_OS_PORT", "8765")))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--data-dir", default=os.environ.get("PERSONAL_OS_DATA_DIR", "./data"))
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    os.environ["PERSONAL_OS_DATA_DIR"] = args.data_dir
    from .logs import register_secret, setup_logging

    register_secret(os.environ.get("PERSONAL_OS_AUTH_TOKEN"))
    register_secret(os.environ.get("FIRECRAWL_API_KEY"))
    setup_logging()
    # log_config=None: uvicorn's own dictConfig would give its loggers private handlers and cut them off from the
    # redacting file handler on the root logger.
    uvicorn.run("personal_os.app:app", host=args.host, port=args.port, reload=args.reload, log_level="info", log_config=None)


if __name__ == "__main__":
    main()
