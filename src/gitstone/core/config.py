"""Settings and counters for one dump."""
import dataclasses
from typing import Callable, Optional

from .log import Logger
from .mp import CTX

DEFAULT_BRANCHES = ["main", "master", "staging", "production", "development"]
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:78.0) Gecko/20100101 Firefox/78.0"
DEFAULT_JOBS = 10
DEFAULT_RETRY = 3
DEFAULT_TIMEOUT = 3


@dataclasses.dataclass(frozen=True)
class Config:
    """Everything a dump needs to know. Immutable: stages that adjust a value
    (e.g. normalising the URL) do so with :meth:`replace`."""

    url: str
    directory: str
    retry: int = DEFAULT_RETRY
    timeout: int = DEFAULT_TIMEOUT
    http_headers: dict = dataclasses.field(default_factory=dict)
    client_cert_p12: Optional[str] = None
    client_cert_p12_password: Optional[str] = None
    method: str = "GET"
    any_status: bool = False
    delay: float = 0.0
    proxy: Optional[str] = None  # as typed by the user; see core.proxy
    log: Logger = dataclasses.field(default_factory=Logger)
    # Polled between requests; returning True aborts the run with Cancelled.
    should_stop: Callable[[], bool] = lambda: False

    def replace(self, **changes):
        return dataclasses.replace(self, **changes)

    def validate(self, jobs=1):
        if jobs < 1:
            raise ValueError("invalid number of jobs, got `%d`" % jobs)
        if self.retry < 1:
            raise ValueError("invalid number of retries, got `%d`" % self.retry)
        if self.timeout < 1:
            raise ValueError("invalid timeout, got `%d`" % self.timeout)
        if self.delay < 0:
            raise ValueError("invalid delay, got `%s`" % self.delay)


class Stats:
    """Counters shared between the main process and the workers."""

    NAMES = ("fetched", "skipped", "failed", "missing_objects", "recovered")

    def __init__(self):
        self._values = {name: CTX.Value("i", 0) for name in self.NAMES}

    def inc(self, name):
        value = self._values[name]
        with value.get_lock():
            value.value += 1

    def set(self, name, n):
        value = self._values[name]
        with value.get_lock():
            value.value = n

    def snapshot(self):
        return {name: value.value for name, value in self._values.items()}
