"""Looking for things in a recovered repository.

Where :mod:`gitstone.viewer` reads a repository and presents it, this package
goes hunting in it: credential patterns and high-entropy strings
(:mod:`secrets`, :mod:`entropy`), text and regex search (:mod:`search`), the
sources a blob scan misses such as ``.git/config`` and commit messages
(:mod:`metadata`), files that Dockerfiles and scripts point at but the
repository does not contain (:mod:`references`), and the pass over the object
store they share (:mod:`walk`). It depends on ``viewer``, never the other way.
"""
