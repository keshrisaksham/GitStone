"""GitStone: recover a git repository from a website's exposed ``.git`` directory.

Layout:

* :mod:`gitstone.core`    -- the dumper itself, as plain functions (no web, no argparse)
* :mod:`gitstone.viewer`  -- read-only inspection of a recovered repository (dulwich)
* :mod:`gitstone.actions` -- the dumper's functions described as data, so a UI can list and run them
* :mod:`gitstone.web`     -- the Flask app that serves those actions over a (local) network
* :mod:`gitstone.cli`     -- the ``gitstone`` command
"""

__version__ = "2.0.0"
