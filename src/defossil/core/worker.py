"""One recurring background job, so a service schedules work without owning thread machinery."""

import threading
from collections.abc import Callable


class Worker:
    """A daemon thread that runs *task* every *interval* seconds; the first run starts right after `start`."""

    def __init__(self, name: str, interval: float, task: Callable[[], None]) -> None:
        """Hold the configuration; nothing runs until `start`. *task* must catch what a failed run may raise."""
        self._name = name
        self._interval = interval
        self._task = task
        self._stopping = threading.Event()
        self._wake = threading.Event()  # cuts the between-runs wait short; set on stop and on wake
        self._thread: threading.Thread | None = None

    @property
    def stopping(self) -> bool:
        """Whether `stop` was called — a task looping over many steps should check this and return early."""
        return self._stopping.is_set()

    def start(self) -> None:
        """Start the thread."""
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Let a running task finish, then wait for the thread.

        No timeout: joining is what keeps the task off the database while Core closes it.
        """
        if self._thread is None:
            return
        self._stopping.set()
        self._wake.set()
        self._thread.join()
        self._thread = None

    def wake(self) -> None:
        """Cut the between-runs wait short so the next run starts immediately."""
        self._wake.set()

    def _run(self) -> None:
        """Run the task, then wait out the interval or a wake, until stopped."""
        while not self._stopping.is_set():
            self._task()
            self._wake.wait(self._interval)
            self._wake.clear()
