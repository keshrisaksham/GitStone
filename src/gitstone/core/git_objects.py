"""Reading git objects to discover which other objects they reference."""
import dulwich.objects

try:
    import dulwich.object_format

    # dulwich >= 1.0 requires the hash algorithm to be passed explicitly
    PACK_KWARGS = {"object_format": dulwich.object_format.DEFAULT_OBJECT_FORMAT}
except ImportError:  # older dulwich
    PACK_KWARGS = {}

GITLINK_MODE = 0o160000  # submodule entry: its commit lives in another repo
ZERO_SHA = "0" * 40


def get_referenced_sha1(obj_file):
    """Return all the SHA1s referenced by the given object."""
    objs = []

    if isinstance(obj_file, dulwich.objects.Commit):
        objs.append(obj_file.tree.decode())

        for parent in obj_file.parents:
            objs.append(parent.decode())
    elif isinstance(obj_file, dulwich.objects.Tree):
        for item in obj_file.iteritems():
            if item.mode == GITLINK_MODE:
                continue  # submodule commit, not stored in this repository

            objs.append(item.sha.decode())
    elif isinstance(obj_file, dulwich.objects.Blob):
        pass
    elif isinstance(obj_file, dulwich.objects.Tag):
        # follow the tag to the object it points at (commit, tree, blob, tag)
        objs.append(obj_file.object[1].decode())
    else:
        # Raise instead of sys.exit(): SystemExit is not caught by the worker's
        # `except Exception`, which would kill the worker and hang the pool.
        raise ValueError("unexpected object type: %r" % obj_file)

    return objs
