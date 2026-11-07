"""Exceptions shared by the dumper."""


class Cancelled(Exception):
    """The caller asked the running dump to stop (see ``Config.should_stop``)."""
