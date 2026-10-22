#!/usr/bin/env python3
"""Attach to a Pod's Delve headless API through kubectl port-forward."""

from __future__ import annotations

import argparse
import json
import queue
import re
import socket
import subprocess
import threading
import time
from typing import BinaryIO


def stop_port_forward(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def start_port_forward(namespace: str, pod: str) -> tuple[subprocess.Popen[str], int]:
    process = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            "--address=127.0.0.1",
            "--namespace",
            namespace,
            f"pod/{pod}",
            ":2345",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()
    recent_output: list[str] = []

    def read_output() -> None:
        for line in process.stdout:
            text = line.rstrip()
            recent_output.append(text)
            lines.put(text)
        lines.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("kubectl port-forward exited: " + " | ".join(recent_output[-5:]))
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("kubectl port-forward closed output before opening a local port")
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 2345$", line)
            if match:
                return process, int(match.group(1))
        raise TimeoutError("timed out waiting for kubectl port-forward: " + " | ".join(recent_output[-5:]))
    except Exception:
        stop_port_forward(process)
        raise


def call_delve(
    conn: socket.socket,
    reader: BinaryIO,
    method: str,
    args: dict,
    request_id: int,
) -> dict:
    request = {"method": method, "params": [args], "id": request_id}
    conn.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
    line = reader.readline()
    if not line:
        raise RuntimeError(f"Delve closed the connection during {method}")
    response = json.loads(line)
    if response.get("error"):
        raise RuntimeError(f"Delve {method} failed: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Delve {method} returned no result object")
    return result


def inspect_debuggee(namespace: str, pod: str) -> int:
    process, local_port = start_port_forward(namespace, pod)
    try:
        with socket.create_connection(("127.0.0.1", local_port), timeout=5) as conn:
            conn.settimeout(10)
            with conn.makefile("rb") as reader:
                state_result = call_delve(
                    conn, reader, "RPCServer.State", {"NonBlocking": True}, 1
                )
                state = state_result.get("State", {})
                if state.get("Running", state.get("running")):
                    raise RuntimeError("debuggee continued before an attach")

                threads_result = call_delve(
                    conn, reader, "RPCServer.ListThreads", {}, 2
                )
                threads = threads_result.get("Threads", threads_result.get("threads", []))
                if not isinstance(threads, list) or not threads:
                    raise RuntimeError("Delve returned no debuggee threads")
                return len(threads)
    finally:
        stop_port_forward(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--pod", required=True)
    args = parser.parse_args()
    try:
        threads = inspect_debuggee(args.namespace, args.pod)
    except (OSError, RuntimeError, TimeoutError, json.JSONDecodeError) as err:
        parser.exit(1, f"debug attach failed for {args.namespace}/{args.pod}: {err}\n")
    print(f"Delve attached to {args.namespace}/{args.pod}: {threads} thread(s), paused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
