from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass


TARGET_NAME = "QwenTeamsInterpreter/DashScope"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


@dataclass(frozen=True)
class SavedCredentials:
    api_key: str
    workspace_id: str


class _CredentialW(ctypes.Structure):
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    )


def _credential_api() -> tuple[object, object, object, object]:
    if os.name != "nt":
        raise OSError("凭据安全存储仅支持 Windows")

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    cred_write = advapi32.CredWriteW
    cred_write.argtypes = (ctypes.POINTER(_CredentialW), wintypes.DWORD)
    cred_write.restype = wintypes.BOOL

    cred_read = advapi32.CredReadW
    cred_read.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CredentialW)),
    )
    cred_read.restype = wintypes.BOOL

    cred_delete = advapi32.CredDeleteW
    cred_delete.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
    cred_delete.restype = wintypes.BOOL

    cred_free = advapi32.CredFree
    cred_free.argtypes = (ctypes.c_void_p,)
    cred_free.restype = None
    return cred_write, cred_read, cred_delete, cred_free


def save_credentials(api_key: str, workspace_id: str) -> None:
    api_key = api_key.strip()
    workspace_id = workspace_id.strip()
    if not api_key or not workspace_id:
        raise ValueError("API Key 和 WorkspaceId 均不能为空")

    cred_write, _, _, _ = _credential_api()
    blob = api_key.encode("utf-16-le")
    blob_buffer = ctypes.create_string_buffer(blob)
    credential = _CredentialW(
        Type=CRED_TYPE_GENERIC,
        TargetName=TARGET_NAME,
        Comment="Teams 千问同声翻译助手",
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(
            blob_buffer, ctypes.POINTER(wintypes.BYTE)
        ),
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        UserName=workspace_id,
    )
    if not cred_write(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def load_credentials() -> SavedCredentials | None:
    _, cred_read, _, cred_free = _credential_api()
    pointer = ctypes.POINTER(_CredentialW)()
    if not cred_read(
        TARGET_NAME,
        CRED_TYPE_GENERIC,
        0,
        ctypes.byref(pointer),
    ):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)

    try:
        credential = pointer.contents
        blob = ctypes.string_at(
            credential.CredentialBlob,
            credential.CredentialBlobSize,
        )
        return SavedCredentials(
            api_key=blob.decode("utf-16-le"),
            workspace_id=credential.UserName or "",
        )
    finally:
        cred_free(ctypes.cast(pointer, ctypes.c_void_p))


def clear_credentials() -> None:
    _, _, cred_delete, _ = _credential_api()
    if not cred_delete(TARGET_NAME, CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise ctypes.WinError(error)
