"""Quiet mode (AGENTS.md section 15.4): less heat, fan noise and battery use on laptops.

`[engine] quiet` is "off", "auto" (on while running on battery) or "on". Nothing here changes what
is checked or how a task is graded; quiet mode only makes the model run gentler and slower.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

import psutil

from qedcode.config import GlobalConfig

QUIET_PAUSE_S = 3.0  # between model calls, so the computer can cool down
QUIET_KEEP_ALIVE_MINUTES = 2  # the model unloads sooner
QUIET_BATCH = 256
QUIET_UBATCH = 128
NICE_VALUE = 10


def on_battery(sensor: Callable[[], Any] = psutil.sensors_battery) -> bool:
    """True when a battery exists and the computer is not plugged in."""
    try:
        battery = sensor()
    except (AttributeError, OSError, psutil.Error):
        return False
    return battery is not None and battery.power_plugged is False


def is_quiet(config: GlobalConfig, battery: Callable[[], Any] = psutil.sensors_battery) -> bool:
    """Is quiet mode active right now?"""
    setting = config.engine.quiet
    if setting == "on":
        return True
    if setting == "auto":
        return on_battery(battery)
    return False


def quiet_threads(cpu_count: int | None = None) -> int:
    """Half of the physical CPU cores for the model server (at least one)."""
    cores = (
        cpu_count
        if cpu_count is not None
        else (psutil.cpu_count(logical=False) or os.cpu_count() or 2)
    )
    return max(1, cores // 2)


def effective_keep_alive(
    config: GlobalConfig, battery: Callable[[], Any] = psutil.sensors_battery
) -> int:
    """Minutes the model stays loaded when idle (shorter in quiet mode)."""
    minutes = config.engine.keep_alive_minutes
    return min(minutes, QUIET_KEEP_ALIVE_MINUTES) if is_quiet(config, battery) else minutes


def pause_seconds(
    config: GlobalConfig, battery: Callable[[], Any] = psutil.sensors_battery
) -> float:
    """Seconds to wait between model calls (0 unless quiet and the model runs on this computer)."""
    if config.engine.mode.value == "cloud":
        return 0.0
    return QUIET_PAUSE_S if is_quiet(config, battery) else 0.0


def lower_priority(pid: int) -> bool:
    """Give the process a lower CPU priority. Returns False when the system refused."""
    try:
        proc = psutil.Process(pid)
        if sys.platform == "win32":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # type: ignore[attr-defined]
        else:
            proc.nice(NICE_VALUE)
        return True
    except (psutil.Error, OSError, AttributeError):
        return False
