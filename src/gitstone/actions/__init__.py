"""The dumper's functions, described as data so a UI can list and run them.

    from gitstone.actions import get, for_kind

    action = get("find_refs")
    values = action.parse({"jobs": "4", "branches": "dev"})
    action.run(RunContext(...), values)
"""
from . import clone, local, remote
from .spec import Action, Param, RunContext

REGISTRY = {a.id: a for a in remote.ACTIONS + clone.ACTIONS + local.ACTIONS}


def get(action_id):
    """The action with this id, or None."""
    return REGISTRY.get(action_id)


def for_kind(kind):
    """Actions applicable to a dump of this kind ("remote", "local" or "cloned")."""
    return [a for a in REGISTRY.values() if kind in a.kinds]


def grouped(kind):
    """``[(group_title, [actions])]`` for a dump of this kind, in display order."""
    groups = {}
    for a in for_kind(kind):
        groups.setdefault(a.group, []).append(a)
    return list(groups.items())


__all__ = ["Action", "Param", "REGISTRY", "RunContext", "for_kind", "get", "grouped"]
