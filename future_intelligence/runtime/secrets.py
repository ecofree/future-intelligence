"""Fail-closed, explicit secret-file injection. No scanning for past credentials."""
from __future__ import annotations
from contextlib import closing
import os
from pathlib import Path
import stat


def _read_windows_file(path):
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32security as security

    try:
        with closing(win32file.CreateFile(str(path), win32con.GENERIC_READ | win32con.READ_CONTROL,
                                         win32file.FILE_SHARE_READ, None, win32file.OPEN_EXISTING,
                                         win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)) as handle:
            if win32file.GetFileType(handle) != win32file.FILE_TYPE_DISK:
                raise ValueError('secret must be a regular file')
            attributes = win32file.GetFileInformationByHandle(handle)[0]
            if attributes & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                raise OSError('indirect secret file rejected')
            info = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
                    security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
            with closing(security.OpenProcessToken(win32api.GetCurrentProcess(), security.TOKEN_QUERY)) as token:
                user = security.GetTokenInformation(token, security.TokenUser)[0]
            owners = (user, security.CreateWellKnownSid(security.WinLocalSystemSid),
                     security.CreateWellKnownSid(security.WinBuiltinAdministratorsSid))
            acl = info.GetSecurityDescriptorDacl()
            if info.GetSecurityDescriptorOwner() not in owners or acl is None:
                raise ValueError('secret file must be private')
            for index in range(acl.GetAceCount()):
                ace = acl.GetAce(index)
                kind, flags = ace[0]
                if kind == security.ACCESS_DENIED_ACE_TYPE or flags & security.INHERIT_ONLY_ACE:
                    continue
                if kind != security.ACCESS_ALLOWED_ACE_TYPE or ace[-1] not in owners:
                    raise ValueError('secret file must be private')
            if not 1 <= win32file.GetFileSize(handle) <= 65536:
                raise ValueError('secret file must be bounded')
            _, data = win32file.ReadFile(handle, 65537)
            if len(data) > 65536:
                raise ValueError('secret file must be bounded')
            return data.decode('utf-8').strip()
    except pywintypes.error:
        raise OSError('secret file unreadable') from None


def read_secret(name: str, environ=None, *, required=True) -> str | None:
    env = os.environ if environ is None else environ
    direct, filename = env.get(name), env.get(name+'_FILE')
    if direct is not None and filename is not None:
        raise ValueError('ambiguous secret source')
    if filename is None:
        value = direct
    elif os.name == 'nt':
        value = _read_windows_file(Path(filename))
    else:
        path = Path(filename)
        # Reject FIFOs/devices after open without blocking on a FIFO writer.
        # O_NOFOLLOW also prevents a final-component symlink swap before open.
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        flags |= getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
        fd = os.open(path, flags)
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
