"""Windows CurrentUser DPAPI. API credentials never enter public device snapshots."""
import ctypes,os
from energy import Error
def transform(raw,encrypt):
 if os.name!='nt':raise Error('当前运行包的 API 凭据保护需要 Windows',503)
 from ctypes import wintypes
 class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
 crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
 fn=getattr(crypt,'CryptProtectData' if encrypt else 'CryptUnprotectData');fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];fn.restype=wintypes.BOOL
 buf=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw);src=Blob(len(raw),buf);out=Blob()
 if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out)):raise Error('API 凭据无法读取，请使用原 Windows 账户或重新填写配置',503)
 try:return ctypes.string_at(out.data,out.size)
 finally:kernel.LocalFree(out.data)
