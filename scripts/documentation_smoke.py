"""Exercise the neutral V3 compatibility quickstart against disposable real local storage.

No external provider/model, production root, official dataset, or secret output.
Requires local loopback binding and a working aiosqlite thread wakeup environment.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mesa-doc-smoke-") as directory:
        lab = Path(directory)
        storage = lab / "storage"
        storage.mkdir()
        env = os.environ.copy()
        env.update(
            MESA_STORAGE_ROOT=str(storage),
            MESA_RUNTIME_PROFILE="combined",
            MESA_LOAD_DOTENV="false",
            MESA_MODEL_ENABLED="false",
            MESA_EXTERNAL_PROVIDER_ENABLED="false",
            MESA_TIER3_MODE="0",
            MESA_PRINCIPAL_ID="docs-principal",
            HF_HUB_OFFLINE="1",
            HF_DATASETS_OFFLINE="1",
        )
        admin = [sys.executable, "-m", "mesa_memory.security.admin_cli"]

        def run(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                arguments,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=90,
                check=True,
            )

        env["MESA_API_KEY"] = run(
            *admin, "issue-key", "--principal", "docs-principal"
        ).stdout.strip()
        run(
            *admin,
            "grant-role",
            "--principal",
            "docs-principal",
            "--tenant",
            "docs-tenant",
            "--role",
            "OWNER",
        )
        run(
            *admin,
            "grant-agent",
            "--principal",
            "docs-principal",
            "--agent",
            "docs-agent",
            "--permission",
            "SESSION_CREATE",
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env["MESA_BASE_URL"] = base
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "mesa_memory.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        retained: dict[str, str | int] | None = None
        for attempt in range(2):
            with (lab / f"server-{attempt}.log").open("w") as log:
                process = subprocess.Popen(
                    command, cwd=ROOT, env=env, stdout=log, stderr=log
                )
                try:
                    with httpx.Client(base_url=base, timeout=10) as http:
                        deadline = time.monotonic() + 90
                        while time.monotonic() < deadline:
                            if process.poll() is not None:
                                raise RuntimeError(
                                    "Disposable server exited before readiness"
                                )
                            try:
                                if http.get("/health/init").status_code == 200:
                                    break
                            except httpx.ConnectError:
                                pass
                            time.sleep(0.25)
                        else:
                            raise RuntimeError("Disposable readiness timeout")
                        require(
                            http.get("/health").status_code == 401,
                            "Missing key was not rejected",
                        )
                        headers = {"X-API-Key": env["MESA_API_KEY"]}
                        require(
                            http.get("/health", headers=headers).status_code == 200,
                            "Authenticated health failed",
                        )
                        if retained is not None:
                            status = http.get(
                                f"/v3/memory/status/{retained['log_id']}",
                                headers=headers,
                                params={"agent_id": "docs-agent"},
                            )
                            status.raise_for_status()
                            require(
                                status.json()["status"].lower() == "processed",
                                "Retained mutation state lost on restart",
                            )
                            restored = http.post(
                                "/v3/memory/search",
                                headers=headers,
                                json={
                                    "agent_id": "docs-agent",
                                    "session_id": retained["session_id"],
                                    "query": "demo archive",
                                    "limit": 5,
                                },
                            )
                            restored.raise_for_status()
                            require(
                                any(
                                    "demo archive" in node.get("content_payload", "")
                                    for node in restored.json()["retrieved_nodes"]
                                ),
                                "Retained scoped evidence lost on restart",
                            )
                        require(
                            http.post(
                                "/v4/memory/search",
                                headers=headers,
                                json={"session_id": "absent", "query": "demo"},
                            ).status_code
                            in {403, 404},
                            "Unknown session was not rejected",
                        )
                        require(
                            http.post(
                                "/v4/memory/insert", headers=headers, json={}
                            ).status_code
                            == 422,
                            "Invalid schema was not rejected",
                        )
                        try:
                            example = run(
                                sys.executable,
                                "-c",
                                "import json; from examples.v3_quickstart import main; "
                                "print(json.dumps(main()))",
                            )
                        except subprocess.CalledProcessError as error:
                            raise RuntimeError(
                                "Neutral example failed: " + error.stderr[-2000:]
                            ) from None
                        require(
                            example.stdout.splitlines()[0]
                            == "PROCESSED; scoped V3 raw memory found",
                            "Neutral example did not validate scoped retrieval",
                        )
                        retained = json.loads(example.stdout.splitlines()[-1])
                        print(
                            f"PASS: {'initial' if attempt == 0 else 'restart'} real local storage, auth, schema and scoped example"
                        )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


if __name__ == "__main__":
    main()
