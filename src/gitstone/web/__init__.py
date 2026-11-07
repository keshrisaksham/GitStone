"""The web UI: a Flask app that lists the dumper's functions, runs them in the
background, and browses what they recover.

    gitstone-ui                 # http://127.0.0.1:5151
    gitstone-ui --lan           # reachable from your local network, with a token
"""
from .app import create_app

__all__ = ["create_app"]
