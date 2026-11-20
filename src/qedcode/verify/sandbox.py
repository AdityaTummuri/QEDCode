"""The sealed test box (AGENTS.md section 15.2): run project tests in Docker with the network off.

Only fixed commands built here run: `docker run`, `docker rm -f`, `docker image inspect`,
`docker build`. The model never chooses any of it. Docker never downloads anything by itself
(`--pull never`); the only download is `docker build` inside `qed sandbox setup`, after asking.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.verify.run import RunResult, run

WORK_DIR = "/work"
OUT_DIR = "/qed-out"
DEFAULT_PYTHON_VERSION = "3.12"
SETUP_HINT = (
    "Run `qed sandbox setup` to build the sealed test box, or `qed sandbox off` to turn it off."
)
DOCKER_HINT = (
    "Start Docker (open Docker Desktop or run the Docker service), or run `qed sandbox off`."
)


def default_image(root: Path | str) -> str:
    """The image tag used when the project config does not name one."""
    digest = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:10]
    return f"qedcode-sandbox-{digest}:latest"


@dataclass(frozen=True)
class DockerSandbox:
    """How to run commands in the container for one project."""

    image: str
    memory: str = "4g"
    cpus: float = 0.0
    docker: str = "docker"

    # ------------------------------------------------------------------ the docker run command

    def _map_path(self, text: str, worktree: Path, run_dir: Path) -> str:
        """Host paths inside an argument become container paths."""
        wt, out = str(worktree), str(run_dir)
        if text == wt or text.startswith(wt + os.sep):
            return WORK_DIR + text[len(wt) :].replace(os.sep, "/")
        if text == out or text.startswith(out + os.sep):
            return OUT_DIR + text[len(out) :].replace(os.sep, "/")
        return text

    def command(
        self,
        inner: Sequence[str],
        *,
        worktree: Path,
        run_dir: Path,
        python: str,
        name: str,
        env: Mapping[str, str] | None = None,
    ) -> list[str]:
        """The full `docker run ...` command for a pytest command written for the host."""
        args = [
            self.docker,
            "run",
            "--rm",
            "--init",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--pids-limit",
            "1024",
            "--memory",
            self.memory,
        ]
        if self.cpus > 0:
            args += ["--cpus", f"{self.cpus:g}"]
        if sys.platform != "win32" and hasattr(os, "getuid"):
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        args += [
            "-v",
            f"{worktree}:{WORK_DIR}",
            "-v",
            f"{run_dir}:{OUT_DIR}",
            "-w",
            WORK_DIR,
            "-e",
            "HOME=/tmp",
        ]
        container_env = dict(env or {})
        for key, value in container_env.items():
            if key == "PYTHONPATH":
                value = ":".join(
                    self._map_path(part, worktree, run_dir) for part in value.split(os.pathsep)
                )
            args += ["-e", f"{key}={value}"]
        mapped = [
            "python" if part == python else self._map_path(part, worktree, run_dir)
            for part in inner
        ]
        return [*args, self.image, *mapped]

    def kill(self, name: str) -> None:
        """Remove a container that outlived its docker client (after a timeout or cancel)."""
        run([self.docker, "rm", "-f", name], timeout=30, scrub=True)

    @staticmethod
    def new_name() -> str:
        return f"qed-{uuid.uuid4().hex[:10]}"

    # ------------------------------------------------------------------ status

    def docker_ready(self) -> tuple[bool, str]:
        """(True, '') when the Docker daemon answers, else (False, what to do)."""
        result = run([self.docker, "info", "--format", "{{.ServerVersion}}"], timeout=30)
        if result.error:
            return False, f"Docker is not installed or not on the PATH. {DOCKER_HINT}"
        if not result.ok:
            return False, f"Docker is installed but not running. {DOCKER_HINT}"
        return True, ""

    def image_exists(self) -> bool:
        return run([self.docker, "image", "inspect", self.image], timeout=30).ok

    def smoke_test(self) -> tuple[bool, str]:
        """Start the image once with the network off and run Python."""
        result = run(
            [
                self.docker,
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                self.image,
                "python",
                "-c",
                "import pytest; print('sealed-ok')",
            ],
            timeout=120,
        )
        if result.ok and "sealed-ok" in result.stdout:
            return True, ""
        return False, (
            "The sealed test box starts but cannot import pytest: "
            f"{result.output[-200:] or 'no output'}. {SETUP_HINT}"
        )


def sandbox_for(project: ProjectConfig, root: Path | str) -> DockerSandbox | None:
    """The sandbox to use for this project, or None when tests run on the host."""
    if project.sandbox != "docker":
        return None
    return DockerSandbox(
        image=project.sandbox_image or default_image(root),
        memory=project.sandbox_memory,
        cpus=project.sandbox_cpus,
    )


def explain_failure(result: RunResult, sandbox: DockerSandbox) -> str:
    """A plain-English reason when the container itself could not run (else '')."""
    if result.error:
        return f"Docker could not be started. {DOCKER_HINT}"
    text = result.output
    low = text.lower()
    if "cannot connect to the docker daemon" in low or "is the docker daemon running" in low:
        return f"The Docker daemon is not running. {DOCKER_HINT}"
    if "unable to find image" in low or "no such image" in low or "pull access denied" in low:
        return f"The sealed test box image '{sandbox.image}' is not built. {SETUP_HINT}"
    if result.exit_code in (125, 126, 127) and "oci runtime" in low:
        return f"Docker could not start the test container: {text[-200:]}"
    if result.exit_code == 125:
        return f"Docker refused to run the test container: {text[-200:]}"
    return ""


# --------------------------------------------------------------------------- building the image


def dockerfile_text(python_version: str, requirement_files: Sequence[str], editable: bool) -> str:
    """The Dockerfile QEDCode generates (never written by the model)."""
    lines = [
        f"FROM python:{python_version}-slim",
        "ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1",
        f"WORKDIR {WORK_DIR}",
        f"COPY . {WORK_DIR}",
        "RUN pip install pytest",
    ]
    for name in requirement_files:
        lines.append(f"RUN pip install -r {name}")
    if editable:
        lines.append('RUN pip install -e ".[dev,test]" || pip install -e .')
    return "\n".join(lines) + "\n"


def requirement_files(root: Path) -> list[str]:
    names = [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements_dev.txt",
        "dev-requirements.txt",
    ]
    return [n for n in names if (root / n).is_file()]


def build_context(root: Path, context: Path, python_version: str) -> Path:
    """Export HEAD into `context` (no .git, .qed or virtualenvs) and write the Dockerfile."""
    from qedcode.vcs.git import git

    shutil.rmtree(context, ignore_errors=True)
    context.mkdir(parents=True)
    archive = context.parent / "head.tar"
    result = git(root, "archive", "--format=tar", "-o", str(archive), "HEAD")
    if not result.ok:
        raise RuntimeError(
            f"Could not export the project for the sealed test box: {result.output[-200:]}. "
            "Check that the repository has a commit, then try again."
        )
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(context, filter="data")
    finally:
        archive.unlink(missing_ok=True)
    for leftover in (".git", ".qed"):  # never part of the image, even if committed
        shutil.rmtree(context / leftover, ignore_errors=True)
    editable = any((context / n).is_file() for n in ("pyproject.toml", "setup.py", "setup.cfg"))
    (context / "Dockerfile").write_text(
        dockerfile_text(python_version, requirement_files(context), editable), encoding="utf-8"
    )
    (context / ".dockerignore").write_text(
        ".git\n.qed\n.venv\nvenv\n__pycache__\n", encoding="utf-8"
    )
    return context
