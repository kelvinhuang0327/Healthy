from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                print(
                    f"Process {process.pid} did not stop after SIGTERM; it was left running.",
                    file=sys.stderr,
                )


def run() -> int:
    api_environment = os.environ.copy()
    web_environment = api_environment.copy()
    web_environment["HEALTHY_API_ORIGIN"] = "http://127.0.0.1:8000"
    processes: list[subprocess.Popen[bytes]] = []
    uv_executable = shutil.which("uv")
    npm_executable = shutil.which("npm")
    if uv_executable is None or npm_executable is None:
        missing_tools = [
            tool_name
            for tool_name, executable in (("uv", uv_executable), ("npm", npm_executable))
            if executable is None
        ]
        print(
            f"Cannot start Healthy; missing {', '.join(missing_tools)}. Run `make install` first.",
            file=sys.stderr,
        )
        return 2

    try:
        # Executables come from PATH; the argument lists below contain only fixed local commands.
        api = subprocess.Popen(  # noqa: S603
            [
                uv_executable,
                "run",
                "--directory",
                "apps/api",
                "uvicorn",
                "healthy.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=ROOT,
            env=api_environment,
            start_new_session=True,
        )
        processes.append(api)

        web = subprocess.Popen(  # noqa: S603
            [npm_executable, "run", "dev", "--workspace", "@healthy/web"],
            cwd=ROOT,
            env=web_environment,
            start_new_session=True,
        )
        processes.append(web)
    except OSError as error:
        print(
            f"Could not start Healthy: {error.strerror}. "
            "Check the installed dependencies and local port availability.",
            file=sys.stderr,
        )
        _stop_processes(processes)
        return 2

    print(
        "Healthy is starting on http://127.0.0.1:3000 (local machine only). Press Ctrl-C to stop."
    )
    try:
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    if return_code != 0:
                        print(
                            "A Healthy service stopped unexpectedly. "
                            "Check the output above for a port or database configuration error.",
                            file=sys.stderr,
                        )
                    return return_code
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopping Healthy.")
        return 0
    finally:
        _stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(run())
