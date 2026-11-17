"""Fail-closed, explicit secret-file injection. No scanning for past credentials."""
from __future__ import annotations
import os
from pathlib import Path
import stat


def read_secret(name: str, environ=None, *, required=True) -> str | None:
    env = os.environ if environ is None else environ
    direct, filename = env.get(name), env.get(name+'_FILE')
    if direct is not None and filename is not None:
        raise ValueError('ambiguous secret source')
    if filename is None:
        value = direct
    else:
        path = Path(filename)
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 1 <= info.st_size <= 65536:
                raise ValueError('secret file must be private, regular and bounded')
            with os.fdopen(fd,'r',encoding='utf-8',closefd=False) as stream:
                value = stream.read(65537).strip()
        finally:
            os.close(fd)
    if not value:
        if required:
            raise ValueError('required secret unavailable')
        return None
    if len(value)>65536 or '\x00' in value:
        raise ValueError('invalid secret value')
    return value


def inject(names, environ=None):
    env = os.environ if environ is None else environ
    result = {}
    for name in names:
        value = read_secret(name,env,required=False)
        if value is not None:
            result[name]=value
    return result
