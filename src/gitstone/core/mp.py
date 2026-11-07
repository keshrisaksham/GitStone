"""The multiprocessing context the dumper uses.

Workers must inherit the parent's state (the open log file, the shared
counters). Python 3.14 defaults to "forkserver", which would lose that, so
the dumper asks for a "fork" context explicitly instead of changing the
process-wide default (which would leak into whatever embeds it, e.g. the
web server).
"""
import multiprocessing

CTX = (
    multiprocessing.get_context("fork")
    if "fork" in multiprocessing.get_all_start_methods()
    else multiprocessing.get_context()
)
