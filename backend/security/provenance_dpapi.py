"""Current-user DPAPI adapter. Importing this module does not load Windows DLLs."""
import ctypes
import hashlib
import sys
from enum import StrEnum

KEY_BYTES = 32
MAX_SEALED_BLOB_BYTES = 16 * 1024
CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4
_ERROR_ACCESS_DENIED = 5
_ERROR_PASSWORD_RESTRICTION = 1325
_ENTROPY = hashlib.sha256(b"sovereignai:provenance:v3:dpapi:entropy").digest()


class DPAPICode(StrEnum):
    UNSUPPORTED = "dpapi_unsupported"
    FLAGS = "dpapi_flags_rejected"
    INPUT = "dpapi_input_invalid"
    OUTPUT = "dpapi_output_invalid"
    UNAVAILABLE = "dpapi_unavailable"
    USER_CONTEXT = "dpapi_user_context_unavailable"
    REJECTED = "dpapi_operation_rejected"


class DPAPIError(RuntimeError):
    def __init__(self, code: DPAPICode):
        self.code = code
        super().__init__(code.value)


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _load_api():
    if sys.platform != "win32":
        raise DPAPIError(DPAPICode.UNSUPPORTED)
    try:
        # LOAD_LIBRARY_SEARCH_SYSTEM32 keeps the current directory out of DLL resolution.
        crypt = ctypes.WinDLL("crypt32.dll", winmode=0x800, use_last_error=True)
        kernel = ctypes.WinDLL("kernel32.dll", winmode=0x800, use_last_error=True)
        protect = crypt.CryptProtectData
        unprotect = crypt.CryptUnprotectData
        common = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                  ctypes.c_uint32, ctypes.POINTER(_DataBlob)]
        protect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, *common]
        unprotect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p), *common]
        protect.restype = unprotect.restype = ctypes.c_int32
        free = kernel.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        return protect, unprotect, free, ctypes.get_last_error
    except (AttributeError, OSError):
        raise DPAPIError(DPAPICode.UNAVAILABLE) from None


class CurrentUserDPAPI:
    def __init__(self, *, flags: int = CRYPTPROTECT_UI_FORBIDDEN):
        if (type(flags) is not int or flags & CRYPTPROTECT_LOCAL_MACHINE
                or flags != CRYPTPROTECT_UI_FORBIDDEN):
            raise DPAPIError(DPAPICode.FLAGS)

    def seal(self, key: bytes) -> bytes:
        if type(key) is not bytes or len(key) != KEY_BYTES:
            raise DPAPIError(DPAPICode.INPUT)
        return self._transform(key, decrypt=False)

    def unseal(self, ciphertext: bytes) -> bytes:
        if type(ciphertext) is not bytes or not 0 < len(ciphertext) <= MAX_SEALED_BLOB_BYTES:
            raise DPAPIError(DPAPICode.INPUT)
        return self._transform(ciphertext, decrypt=True)

    @staticmethod
    def _transform(data: bytes, *, decrypt: bool) -> bytes:
        protect, unprotect, free, last_error = _load_api()
        source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        entropy = (ctypes.c_ubyte * len(_ENTROPY)).from_buffer_copy(_ENTROPY)
        source_blob = _DataBlob(len(source), source)
        entropy_blob = _DataBlob(len(entropy), entropy)
        result = _DataBlob()
        operation = unprotect if decrypt else protect
        succeeded = False
        try:
            succeeded = bool(operation(ctypes.byref(source_blob), None, ctypes.byref(entropy_blob),
                                       None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result)))
            if not succeeded:
                context_error = last_error() in (_ERROR_ACCESS_DENIED, _ERROR_PASSWORD_RESTRICTION)
                code = DPAPICode.USER_CONTEXT if context_error else DPAPICode.REJECTED
                raise DPAPIError(code)
            if (not result.data or not 0 < result.size <= MAX_SEALED_BLOB_BYTES
                    or (decrypt and result.size != KEY_BYTES)):
                raise DPAPIError(DPAPICode.OUTPUT)
            return ctypes.string_at(result.data, result.size)
        finally:
            ctypes.memset(source, 0, len(source))
            if result.data:
                try:
                    # Only successful DPAPI calls guarantee an allocated buffer of this size.
                    if succeeded:
                        ctypes.memset(result.data, 0, result.size)
                finally:
                    free(result.data)
