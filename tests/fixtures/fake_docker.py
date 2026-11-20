"""A stand-in for the `docker` command, so the sealed test box is tested without Docker.

It understands: info, image inspect, build, run, rm. State lives in the folder named by the
FAKE_DOCKER_STATE environment variable: images.json (built images), calls.jsonl (every call),
containers/<name>.pid, and marker files that make it misbehave: `down` (daemon not running),
`build_fails`. `docker run` runs the container's command on this computer: /work and /qed-out are
mapped to the mounted folders and `python` is the interpreter running this script.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

STATE = Path(os.environ["FAKE_DOCKER_STATE"])
ARGV = sys.argv[1:]
FLAGS_WITH_VALUE = {
    "--name", "--pull", "--network", "--cap-drop", "--security-opt", "--tmpfs", "--pids-limit",
    "--memory", "--cpus", "--user", "-v", "-w", "-e", "--format", "-t",
}  # fmt: skip


def record(entry: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def images() -> list[str]:
    path = STATE / "images.json"
    return json.loads(path.read_text()) if path.exists() else []


def add_image(name: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "images.json").write_text(json.dumps([*images(), name]))


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    if (STATE / "down").exists() and ARGV[:1] != ["rm"]:
        record({"cmd": ARGV[0], "argv": ARGV, "refused": "down"})
        fail(
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
            "Is the docker daemon running?"
        )
    cmd = ARGV[0]
    if cmd == "info":
        record({"cmd": "info", "argv": ARGV})
        print("27.0.0")
    elif cmd == "image" and ARGV[1] == "inspect":
        record({"cmd": "image_inspect", "argv": ARGV})
        if ARGV[2] not in images() and ARGV[2] != os.environ.get("FAKE_DOCKER_BASE", "-"):
            fail(f"Error: No such image: {ARGV[2]}")
    elif cmd == "build":
        build(ARGV[1:])
    elif cmd == "run":
        run_container(ARGV[1:])
    elif cmd == "rm":
        name = ARGV[-1]
        record({"cmd": "rm", "argv": ARGV, "name": name})
        pid_file = STATE / "containers" / f"{name}.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 9)
            except (OSError, ValueError):
                pass
    else:
        fail(f"fake docker: unknown command {cmd}", 2)


def build(args: list[str]) -> None:
    tag = args[args.index("-t") + 1]
    context = Path(args[-1])
    dockerfile = (context / "Dockerfile").read_text()
    record({"cmd": "build", "argv": args, "tag": tag, "dockerfile": dockerfile,
            "files": sorted(p.name for p in context.iterdir())})  # fmt: skip
    if (STATE / "build_fails").exists():
        fail("ERROR: failed to solve: process \"/bin/sh -c pip install -r requirements.txt\" "
             "did not complete successfully: exit code: 1")  # fmt: skip
    add_image(tag)
    print(f"Successfully tagged {tag}")


def run_container(args: list[str]) -> None:
    opts: dict[str, list[str]] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-"):
            break
        if arg in FLAGS_WITH_VALUE:
            opts.setdefault(arg, []).append(args[index + 1])
            index += 2
        else:
            opts.setdefault(arg, []).append("")
            index += 1
    image, command = args[index], args[index + 1 :]
    record({"cmd": "run", "argv": ARGV, "opts": opts, "image": image, "command": command})
    if opts.get("--network") != ["none"]:
        fail("fake docker: the test box must run with --network none", 3)
    if opts.get("--pull") != ["never"]:
        fail("fake docker: the test box must use --pull never", 3)
    if image not in images():
        fail(f"Unable to find image '{image}' locally\ndocker: Error response from daemon: "
             "pull access denied", 125)  # fmt: skip
    mounts = {}
    for spec in opts.get("-v", []):
        host, container = spec.rsplit(":", 1)
        mounts[container] = host

    def host(text: str) -> str:
        for container in sorted(mounts, key=len, reverse=True):
            if text == container or text.startswith(container + "/"):
                return mounts[container] + text[len(container) :]
        return text

    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    for item in opts.get("-e", []):
        key, _, value = item.partition("=")
        env[key] = (
            ":".join(host(part) for part in value.split(":")) if key == "PYTHONPATH" else value
        )
    inner = [sys.executable if part == "python" else host(part) for part in command]
    cwd = host(opts.get("-w", ["/"])[0])
    proc = subprocess.Popen(inner, cwd=cwd, env=env)  # noqa: S603
    pid_dir = STATE / "containers"
    pid_dir.mkdir(parents=True, exist_ok=True)
    if "--name" in opts:
        (pid_dir / f"{opts['--name'][0]}.pid").write_text(str(proc.pid))
    raise SystemExit(proc.wait())


main()
