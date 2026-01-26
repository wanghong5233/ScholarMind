"""Code execution client for local Python snippets."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, Optional


class CodeExecClient:
    """Execute Python snippets in a subprocess."""

    def __init__(self, timeout_seconds: int, max_output_chars: int, max_code_chars: int) -> None:
        """Initialize execution limits."""

        self._timeout_seconds = max(1, timeout_seconds)
        self._max_output_chars = max(200, max_output_chars)
        self._max_code_chars = max(200, max_code_chars)

    async def execute(self, code: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Execute Python code in a separate process.

        Args:
            code (str): Python code snippet.
            timeout (Optional[int]): Override timeout seconds.

        Returns:
            Dict[str, Any]: Execution results.
        """

        if len(code or "") > self._max_code_chars:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Code snippet exceeds maximum length.",
                "exit_code": None,
                "duration_ms": 0,
            }

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, "-c", code]
        loop = asyncio.get_running_loop()
        start = loop.time()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        exec_timeout = timeout or self._timeout_seconds
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=exec_timeout)
        except asyncio.TimeoutError:
            process.kill()
            return {
                "success": False,
                "stdout": "",
                "stderr": "Execution timeout.",
                "exit_code": None,
                "duration_ms": int((loop.time() - start) * 1000),
            }
        duration_ms = int((loop.time() - start) * 1000)
        stdout_text = (stdout or b"").decode("utf-8", errors="ignore")
        stderr_text = (stderr or b"").decode("utf-8", errors="ignore")
        stdout_text = stdout_text[: self._max_output_chars]
        stderr_text = stderr_text[: self._max_output_chars]
        return {
            "success": process.returncode == 0,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": process.returncode,
            "duration_ms": duration_ms,
        }
