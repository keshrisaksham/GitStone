"""Read-only inspection of a recovered repository, through dulwich only.

There is no ``git`` subprocess and no shell anywhere in here: refs, paths and
SHAs that originate in a browser are only ever used as dulwich lookup keys.
"""
