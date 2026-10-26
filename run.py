"""Start RePulse on localhost. VS Code Run App uses this entry point."""

import argparse

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RePulse 复脉 · 运动恢复监护系统")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("backend.api.app:app", host="127.0.0.1", port=args.port, workers=1, log_level="info")
