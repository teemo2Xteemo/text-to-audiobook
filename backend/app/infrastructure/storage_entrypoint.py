"""Docker entrypoint: chown the storage bind-mount, drop to ``app``, then exec.

Compose mounts ``./storage`` at ``STORAGE_PATH``. Docker often creates that host
directory as root, so uid 1000 cannot write unless this process starts as root,
fixes ownership, and drops privileges before the API or worker command.
"""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

_APP_USER = "app"
_DEFAULT_STORAGE = Path("/data/storage")


def storage_path_from_env(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = env.get("STORAGE_PATH", str(_DEFAULT_STORAGE)).strip() or str(_DEFAULT_STORAGE)
    return Path(raw)


def prepare_storage(path: Path, uid: int, gid: int) -> Path:
    """Create ``path`` if needed and ``lchown`` the tree. Does not follow symlinks.

    Use lexical ``abspath`` (not ``Path.resolve``) so a ``STORAGE_PATH`` that is
    itself a symlink is chowned as the link, not its target.
    """
    configured = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    if configured == Path("/"):
        raise SystemExit("STORAGE_PATH must not be /")
    if configured.is_symlink():
        os.lchown(configured, uid, gid)
        return configured
    configured.mkdir(parents=True, exist_ok=True)
    chown_tree(configured, uid, gid)
    return configured


def chown_tree(root: Path, uid: int, gid: int) -> None:
    os.lchown(root, uid, gid)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            os.lchown(os.path.join(dirpath, name), uid, gid)


def drop_privileges(username: str = _APP_USER) -> pwd.struct_passwd:
    pw = pwd.getpwnam(username)
    os.environ["HOME"] = pw.pw_dir
    os.environ["USER"] = pw.pw_name
    os.environ["LOGNAME"] = pw.pw_name
    os.initgroups(pw.pw_name, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    return pw


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        raise SystemExit("storage_entrypoint: missing command")
    if os.geteuid() == 0:
        pw = pwd.getpwnam(_APP_USER)
        prepare_storage(storage_path_from_env(), pw.pw_uid, pw.pw_gid)
        drop_privileges(_APP_USER)
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
