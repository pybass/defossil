"""Background mode: `start` detaches a server, `stop` and `status` find it again through the pid file.

The file is only a note of pid and port; the flock on it is what says a server is running. A crash leaves the file
behind but not the lock. Nothing deletes the file: unlinking a lock file lets two servers each lock their own copy.
"""

import fcntl
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def claim(data_dir: Path, port: int) -> Generator[None]:
    """Hold *data_dir* as its one server for the length of the block; exit when another server holds it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    # "a+", not "w": opening must not wipe the note of a running server.
    with (data_dir / "defossil.pid").open("a+") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise SystemExit(f"another defossil already runs on {data_dir}") from e
        file.truncate(0)
        file.write(f"{os.getpid()} {port}")
        file.flush()
        yield


def running(data_dir: Path) -> tuple[int, int] | None:
    """Pid and port of the server on *data_dir*, or None when there is none."""
    path = data_dir / "defossil.pid"
    if not path.exists():
        return None
    with path.open() as file:
        try:
            fcntl.flock(file, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pid, port = file.read().split()
            return int(pid), int(port)
    return None


def start(data_dir: Path, port: int) -> None:
    """Run the server detached from this terminal and return once it answers."""
    if instance := running(data_dir):
        running_pid, running_port = instance
        logger.info(f"already running: http://127.0.0.1:{running_port} (pid {running_pid})")
        return
    # Checked before the launch: afterwards an answer on the port must mean our server, not someone else's.
    if _listening(port):
        raise SystemExit(f"port {port} is already in use")
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "defossil.log"
    with log_path.open("a") as log:
        child = subprocess.Popen(  # noqa: S603 -- fixed argv, our own interpreter, no shell
            [sys.executable, "-m", "defossil", "--data-dir", str(data_dir), "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,  # its own session, so closing the terminal does not take the server with it
        )
    deadline = time.monotonic() + 10
    while not _listening(port):
        if child.poll() is not None or time.monotonic() > deadline:
            child.terminate()
            raise SystemExit(f"failed to start, see {log_path}")
        time.sleep(0.1)
    logger.info(f"started: http://127.0.0.1:{port} (pid {child.pid}), log: {log_path}")


def stop(data_dir: Path) -> None:
    """Ask the server to stop and wait until it has. No deadline: the server lets an AI call in flight finish first."""
    instance = running(data_dir)
    if instance is None:
        logger.info("not running")
        return
    logger.info(f"stopping pid {instance[0]}")
    os.kill(instance[0], signal.SIGTERM)
    while running(data_dir):
        time.sleep(0.1)
    logger.info("stopped")


def status(data_dir: Path) -> None:
    """Report the server on *data_dir*; exit with 1 when there is none."""
    instance = running(data_dir)
    if instance is None:
        raise SystemExit("not running")
    pid, port = instance
    logger.info(f"running: http://127.0.0.1:{port} (pid {pid})")


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0
