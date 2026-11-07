"""Where the dumper's progress output goes.

The dumper used to write through module-level globals (``QUIET`` and
``LOG_STREAM``), which made it impossible to run two dumps with different
log destinations in one process. A :class:`Logger` is now carried inside the
:class:`~gitstone.core.config.Config`, so each run owns its output.
"""
import sys


class Logger:
    """Writes progress to ``stream`` and problems to ``err_stream``.

    Both default to the *current* ``sys.stdout`` / ``sys.stderr`` at the time
    of each write (not at construction), so redirection and test capture work.
    """

    def __init__(self, stream=None, err_stream=None, quiet=False):
        self.stream = stream
        self.err_stream = err_stream
        self.quiet = quiet

    @staticmethod
    def _emit(stream, fmt, args):
        if args:
            fmt = fmt % args
        stream.write(fmt)
        stream.flush()

    def out(self, fmt, *args):
        """Always shown (summary, warnings that are not errors)."""
        self._emit(self.stream or sys.stdout, fmt, args)

    def info(self, fmt, *args):
        """Per-request progress; hidden by ``quiet``."""
        if not self.quiet:
            self.out(fmt, *args)

    def warn(self, fmt, *args):
        """Errors and skipped items; always shown, on the error stream."""
        self._emit(self.err_stream or sys.stderr, fmt, args)
