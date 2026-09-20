#!/usr/bin/env python3
"""Start the local API and web UI, terminating only this launcher's child processes."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

def npm():
    cli = os.getenv('NPM_CLI')
    node = shutil.which('node')
    if cli and node:
        return [node, cli]
    found = shutil.which('npm')
    if not found:
        raise SystemExit('Node.js 24 and npm must be on PATH. Alternatively set NPM_CLI to npm-cli.js.')
    return [found]

def main():
    python = ROOT / 'backend/.venv/bin/python'
    if not python.exists():
        raise SystemExit('Create coding/backend/.venv and install requirements first; see coding/README.md.')
    commands = [
        (ROOT / 'backend', [str(python), '-m', 'uvicorn', 'app.main:app', '--host', os.getenv('API_HOST', '127.0.0.1'), '--port', '8037']),
        (ROOT / 'frontend', npm() + ['run', 'dev', '--', '--host', '127.0.0.1', '--port', '5137']),
    ]
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for cwd, command in commands:
            children.append(subprocess.Popen(command, cwd=cwd, start_new_session=True))
        print('Web: http://127.0.0.1:5137 | API: http://127.0.0.1:8037/docs', flush=True)
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise SystemExit('One service stopped; see its output above.')
            time.sleep(.3)
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()

if __name__ == '__main__':
    main()
