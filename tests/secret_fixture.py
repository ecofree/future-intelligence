"""Set real filesystem permissions on disposable test inputs."""
import os
import unittest
from contextlib import closing


def grant_test_access(path, *, public=False):
    if os.name != 'nt':
        path.chmod(0o644 if public else 0o600)
        return
    import win32api
    import ntsecuritycon
    import win32security as security
    with closing(security.OpenProcessToken(win32api.GetCurrentProcess(), security.TOKEN_QUERY)) as token:
        owner = security.GetTokenInformation(token, security.TokenUser)[0]
    acl = security.ACL()
    acl.AddAccessAllowedAce(security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, owner)
    if public:
        acl.AddAccessAllowedAce(security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
                                security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
                                  | security.PROTECTED_DACL_SECURITY_INFORMATION, None, None, acl, None)


def file_symlink(link, target):
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == 'nt' and exc.winerror == 1314:
            raise unittest.SkipTest('Windows user lacks the file-symlink privilege') from None
        raise
