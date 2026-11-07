"""Lets this run straight from a checkout with no install step:

    cd GitStone-Packages && python3 -m gitstone_webui [options]

(run from inside GitStone-Packages/, not the repo root, so that `-m` can
find this package) -- same as the `gitstone-ui` console script, just
without needing `pip install` first -- handy for an offline/air-gapped box
where you only copied the repo over. Requires Flask and gitstone.py's own
dependencies to already be importable (`pip install -r
Setup-Packages/requirements-local.txt`, or run from a checkout that
already has them)."""
from .app import main

if __name__ == "__main__":
    main()
