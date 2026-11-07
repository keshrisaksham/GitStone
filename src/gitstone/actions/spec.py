"""What an *action* is: one runnable function plus a description of its inputs.

An action is data, not a view. The web UI turns the ``params`` of an action
into a form and calls ``run``; nothing in here knows about Flask, and the
same registry could drive a CLI sub-command or a JSON API.
"""
import dataclasses
from typing import Callable, Mapping

from ..core import Logger, Stats

KINDS = ("text", "int", "float", "bool", "textarea", "password", "choice")


@dataclasses.dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str = "text"
    default: object = ""
    help: str = ""
    required: bool = False
    choices: tuple = ()
    secret: bool = False  # never persisted with the run's record
    advanced: bool = False  # tucked into a collapsed section of the form
    placeholder: str = ""
    minimum: float = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError("unknown parameter kind %r" % self.kind)

    def coerce(self, raw):
        """Turn a submitted string (or None when absent) into the typed value.
        Raises ValueError with a message fit to show the user."""
        if self.kind == "bool":
            return str(raw).strip().lower() in ("1", "true", "on", "yes") if raw else False

        if raw is None or str(raw).strip() == "":
            if self.required:
                raise ValueError("%s is required" % self.label)
            return self.default

        text = str(raw)
        if self.kind in ("int", "float"):
            try:
                value = int(text) if self.kind == "int" else float(text)
            except ValueError:
                raise ValueError("%s must be a number, got %r" % (self.label, text))
            if self.minimum is not None and value < self.minimum:
                raise ValueError("%s must be at least %s" % (self.label, self.minimum))
            return value

        if self.kind == "choice":
            if text not in self.choices:
                raise ValueError("%s must be one of %s" % (self.label, ", ".join(self.choices)))
            return text

        if self.kind == "password":
            return text  # exact: whitespace may be part of a password
        return text.strip() if self.kind == "text" else text


@dataclasses.dataclass
class RunContext:
    """What an action is given to work with."""

    url: str  # the target URL (or, for a local import, the source path)
    directory: str  # where the recovered repository lives
    log: Logger
    stats: Stats
    should_stop: Callable[[], bool] = lambda: False


@dataclasses.dataclass(frozen=True)
class Action:
    id: str
    title: str
    summary: str
    group: str
    run: Callable[[RunContext, dict], int]  # returns an exit code, 0 = success
    params: tuple = ()
    kinds: tuple = ("remote",)  # which dumps it applies to: "remote" and/or "local"
    network: bool = False  # does it talk to the target?

    def parse(self, raw: Mapping) -> dict:
        """Validate submitted values (strings) against the declared params."""
        return {p.name: p.coerce(raw.get(p.name)) for p in self.params}

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.params}

    def public_values(self, values: Mapping) -> dict:
        """``values`` without the secret ones: safe to store and display."""
        secret = {p.name for p in self.params if p.secret}
        return {k: v for k, v in values.items() if k not in secret}
