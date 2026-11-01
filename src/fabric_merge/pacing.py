"""A per-API-instance start-rate floor for every DAX query path."""

import math
from threading import Lock
import time

from .util import require


class QueryPacer:
    def __init__(self, interval=0.55, *, clock=None, sleep=None):
        require(
            isinstance(interval, (int, float))
            and math.isfinite(interval)
            and interval >= 0.55,
            "DAX query spacing must be finite and at least 0.55 seconds",
        )
        self.interval = interval
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last_start = None
        self.lock = Lock()

    def wait(self):
        with self.lock:
            if self.last_start is not None:
                # Recheck after sleep: interruption/early wakeup must not defeat the floor.
                deadline = self.last_start + self.interval
                while (remaining := deadline - self.clock()) > 0:
                    self.sleep(remaining)
            self.last_start = self.clock()
