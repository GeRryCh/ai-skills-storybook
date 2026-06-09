"""E2E test support: start/stop the edit_story.py server against a temp fixture copy.

The server saves story.json in place. Tests must never use the committed fixture
directly — always copy to tmp so the fixture stays clean.
"""
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
EDIT_STORY_SCRIPT = (
    REPO_ROOT / "skills" / "storybook-story" / "scripts" / "edit_story.py"
)


class EditorServer:
    """Manages one edit_story.py server against a temp copy of a named fixture.

    Usage (context manager)::

        with EditorServer("pip-storm") as server:
            # server.url  → "http://127.0.0.1:{port}/"
            ...

    Or manual::

        server = EditorServer("pip-storm").start()
        ...
        server.stop()
    """

    def __init__(self, fixture_name: str) -> None:
        self.fixture_name = fixture_name
        self._tmp_dir: str | None = None
        self._proc: subprocess.Popen | None = None
        self.url: str | None = None

    # ------------------------------------------------------------------

    def start(self) -> "EditorServer":
        self._tmp_dir = tempfile.mkdtemp(prefix="storybook_e2e_")
        src = FIXTURES / self.fixture_name
        tmp_fixture = Path(self._tmp_dir) / self.fixture_name
        shutil.copytree(src, tmp_fixture)

        # Strip API keys — browse + save need none; tests must not call paid endpoints.
        # PYTHONUNBUFFERED=1: the server's print() calls are fully-buffered when stdout
        # is a pipe; without unbuffering, the URL line never leaves the buffer during
        # serve_forever() and _read_url() would always time out.
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("GEMINI_API_KEY", "OPENAI_API_KEY",
                         "STORYBOOK_SKILL_OPENAI_API_KEY")
        }
        env["PYTHONUNBUFFERED"] = "1"

        self._proc = subprocess.Popen(
            [
                "uv", "run", str(EDIT_STORY_SCRIPT),
                "--story", str(self.story_path),
                "--no-browser", "--port", "0",
            ],
            stdout=subprocess.PIPE,
            # Discard stderr: BaseHTTPRequestHandler writes one line per request.
            # If we pipe stderr and don't read it, the OS buffer fills and the
            # server blocks — preventing the URL from being printed to stdout.
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )

        self.url = self._read_url(timeout=30)
        if not self.url:
            self.stop()
            raise RuntimeError(
                f"Editor server ({self.fixture_name!r}) did not start within 30 s."
            )
        return self

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        if self._tmp_dir:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        self.url = None

    def __enter__(self) -> "EditorServer":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    @property
    def story_path(self) -> Path:
        assert self._tmp_dir, "Server not started"
        return Path(self._tmp_dir) / self.fixture_name / "story.json"

    # ------------------------------------------------------------------

    def _read_url(self, timeout: float) -> str | None:
        """Read stdout until the 'Open http://...' line; return the URL or None on timeout."""
        result: list[str | None] = [None]

        def _reader() -> None:
            if not (self._proc and self._proc.stdout):
                return
            for line in self._proc.stdout:
                m = re.search(r"(http://127\.0\.0\.1:\d+/)", line)
                if m:
                    result[0] = m.group(1)
                    return

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        return result[0]
