"""Set up, check and turn off the sealed test box (AGENTS.md section 15.2).

`setup` is the only step that uses the network (docker build). It asks first and explains what it
will download. Nothing here is ever run by the model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import load_project, save_project
from qedcode.project.detect import find_python, probe_python
from qedcode.verify.run import run
from qedcode.verify.sandbox import (
    DEFAULT_PYTHON_VERSION,
    DockerSandbox,
    build_context,
    default_image,
    sandbox_for,
)

BASE_IMAGE_MB = 150
BUILD_TIMEOUT_S = 1800


@dataclass
class SandboxStatus:
    mode: str  # "off" or "docker"
    image: str
    docker_ready: bool
    docker_message: str = ""
    image_built: bool = False

    @property
    def ready(self) -> bool:
        return self.mode == "docker" and self.docker_ready and self.image_built

    def describe(self) -> str:
        if self.mode == "off":
            return (
                "The sealed test box is off: tests run on this computer. "
                "Turn it on with `qed sandbox setup` (needs Docker)."
            )
        if not self.docker_ready:
            return f"The sealed test box is on, but Docker is not usable. {self.docker_message}"
        if not self.image_built:
            return (
                f"The sealed test box is on, but its image '{self.image}' is not built. "
                "Run `qed sandbox setup`."
            )
        return f"The sealed test box is on and ready (image {self.image}, network off)."


@dataclass
class SetupResult:
    ok: bool
    message: str
    image: str = ""


def status(root: Path | str, *, sandbox: DockerSandbox | None = None) -> SandboxStatus:
    folder = Path(root)
    project = load_project(folder)
    box = sandbox or sandbox_for(project, folder)
    if box is None:
        return SandboxStatus("off", project.sandbox_image or default_image(folder), True)
    ready, message = box.docker_ready()
    built = ready and box.image_exists()
    return SandboxStatus("docker", box.image, ready, message, built)


def python_version_for(root: Path, python: str) -> str:
    """'3.12' for the project's interpreter (the base image tag); a default if it cannot be run."""
    version, _pytest = probe_python(python, root)
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 and parts[0].isdigit() else DEFAULT_PYTHON_VERSION


def setup(
    root: Path | str,
    confirm: Callable[[str], bool],
    on_progress: Callable[[str], None] | None = None,
    *,
    docker: str = "docker",
) -> SetupResult:
    """Build the image, check it works with the network off, and turn the sealed test box on."""
    folder = Path(root)
    say = on_progress or (lambda _text: None)
    project = load_project(folder)
    image = project.sandbox_image or default_image(folder)
    box = DockerSandbox(
        image=image, memory=project.sandbox_memory, cpus=project.sandbox_cpus, docker=docker
    )
    ready, message = box.docker_ready()
    if not ready:
        return SetupResult(False, message)
    python = project.python or find_python(folder)[0]
    version = python_version_for(folder, python)
    base = f"python:{version}-slim"
    have_base = run([docker, "image", "inspect", base], timeout=30).ok
    base_note = (
        f"the base image {base} is already on this computer"
        if have_base
        else f"it downloads the base image {base} (about {BASE_IMAGE_MB} MB)"
    )
    if not confirm(
        "QEDCode will build a Docker image so that tests run in a sealed box with no network.\n"
        f"- {base_note}\n"
        "- it installs pytest and your project's dependencies with pip (needs internet, "
        "only during this build; the size depends on your dependencies)\n"
        "Tests afterwards run with the network off. Continue?"
    ):
        return SetupResult(
            False, "Cancelled. Nothing was downloaded and the sealed test box is unchanged."
        )
    say("Exporting your project (the last commit) ...")
    try:
        context = build_context(folder, folder / ".qed" / "sandbox" / "context", version)
    except RuntimeError as exc:
        return SetupResult(False, str(exc))
    say("Building the sealed test box (this can take several minutes) ...")
    log = folder / ".qed" / "sandbox" / "build.log"
    built = run([docker, "build", "-t", image, str(context)], timeout=BUILD_TIMEOUT_S, log_path=log)
    if not built.ok:
        last = "\n".join(built.output.strip().splitlines()[-5:])
        return SetupResult(
            False,
            f"The Docker build failed: {last or built.error or 'no output'}\n"
            f"The full log is in {log}. Fix the problem, then run `qed sandbox setup` again.",
        )
    say("Checking that tests can run with the network off ...")
    works, why = box.smoke_test()
    if not works:
        return SetupResult(False, why)
    project.sandbox = "docker"
    project.sandbox_image = image
    save_project(folder, project)
    return SetupResult(
        True,
        f"The sealed test box is ready (image {image}). Tests now run with the network off.",
        image,
    )


def turn_off(root: Path | str) -> bool:
    """Switch back to running tests on this computer. False when it was already off."""
    folder = Path(root)
    project = load_project(folder)
    if project.sandbox == "off":
        return False
    project.sandbox = "off"
    save_project(folder, project)
    return True
