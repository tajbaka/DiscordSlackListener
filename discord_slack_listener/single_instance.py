from __future__ import annotations

import atexit
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil


@dataclass(frozen=True)
class PidRecord:
    pid: int
    marker: str
    created_at: float | None = None


class SingleInstanceGuard:
    def __init__(
        self,
        *,
        pidfile: Path,
        marker: str,
        logger,
        match_process: Callable[[psutil.Process], bool] | None = None,
    ):
        self.pidfile = pidfile
        self.marker = marker
        self.logger = logger
        self.match_process = match_process
        self.claimed = False

    def acquire(self) -> None:
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        prior = self._read_record()
        if prior and prior.pid != os.getpid() and self._is_record_alive(prior):
            self.logger.warning(
                "Found prior %s instance pid=%s; terminating it before startup",
                self.marker,
                prior.pid,
            )
            self._terminate_process_tree(prior.pid)

        if self.match_process is not None:
            self._terminate_legacy_matches()

        self._write_record()
        self.claimed = True
        atexit.register(self.release)

    def release(self) -> None:
        if not self.claimed:
            return
        try:
            current = self._read_record()
            if current and current.pid == os.getpid():
                self.pidfile.unlink(missing_ok=True)
        finally:
            self.claimed = False

    def _read_record(self) -> PidRecord | None:
        try:
            payload = json.loads(self.pidfile.read_text())
            return PidRecord(
                pid=int(payload["pid"]),
                marker=str(payload["marker"]),
                created_at=(
                    float(payload["created_at"])
                    if payload.get("created_at") is not None
                    else None
                ),
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            self.logger.debug("Could not read pidfile %s: %s", self.pidfile, exc)
            return None

    def _write_record(self) -> None:
        payload = {
            "pid": os.getpid(),
            "marker": self.marker,
            "created_at": psutil.Process(os.getpid()).create_time(),
        }
        tmp = self.pidfile.with_suffix(self.pidfile.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.pidfile)

    def _is_record_alive(self, record: PidRecord) -> bool:
        try:
            proc = psutil.Process(record.pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return False
            if record.created_at is not None and abs(proc.create_time() - record.created_at) > 1:
                return False
            return record.marker == self.marker and record.marker in " ".join(proc.cmdline())
        except psutil.Error:
            return False

    def _terminate_legacy_matches(self) -> None:
        own_tree = {os.getpid()}
        try:
            own = psutil.Process(os.getpid())
            own_tree.update(parent.pid for parent in own.parents())
        except psutil.Error:
            pass

        victims: list[int] = []
        for proc in psutil.process_iter(["pid"]):
            try:
                if proc.pid in own_tree:
                    continue
                if self.match_process and self.match_process(proc):
                    victims.append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        for pid in victims:
            self.logger.warning(
                "Found prior %s process pid=%s; terminating it before startup",
                self.marker,
                pid,
            )
            self._terminate_process_tree(pid)

    def _terminate_process_tree(self, pid: int) -> None:
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return

        victims = parent.children(recursive=True)
        victims.append(parent)
        for proc in victims:
            try:
                proc.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(victims, timeout=5)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=2)
        self.logger.info("Stopped prior %s process tree rooted at pid=%s", self.marker, pid)
