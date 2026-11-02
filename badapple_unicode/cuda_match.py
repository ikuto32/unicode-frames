"""Optional native exact uint8 L2 matching; no PyTorch dependency required."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path

import numpy as np

BUILD = Path(__file__).resolve().parents[1] / "cuda/unicode-grid/build/current.json"


def native_available() -> bool:
    return BUILD.is_file()


def exact_u8_search(queries: np.ndarray, candidates: np.ndarray, query_batch: int = 256,
                    candidate_batch: int = 32768) -> tuple[np.ndarray,np.ndarray,str]:
    if (queries.ndim!=2 or candidates.ndim!=2 or queries.shape[1]!=candidates.shape[1]
            or queries.dtype!=np.uint8 or candidates.dtype!=np.uint8 or not len(candidates)
            or not 0<queries.shape[1]<=32000 or queries.shape[1]%4):
        raise ValueError("Native search expects uint8 matrices with equal widths divisible by four")
    if query_batch<4 or candidate_batch<4:
        raise ValueError("Native batch sizes must be at least four")
    if not len(queries):
        return np.empty(0,np.int64),np.empty(0,np.float32),"native-int8"
    if not BUILD.is_file():
        raise FileNotFoundError("Build the native matcher with cuda/unicode-grid/build.ps1, or use --backend faiss")
    build = json.loads(BUILD.read_text(encoding="utf-8-sig"))
    root = Path(build["cuda_root"])
    directories = ([os.add_dll_directory(str(path)) for path in [root/"bin",root/"bin/x64"] if path.is_dir()]
                   if os.name=="nt" else [])
    try:
        library = ctypes.CDLL(build["library"])
        library.unicode_match_error.restype = ctypes.c_char_p
        byte = ctypes.POINTER(ctypes.c_uint8)
        integer = ctypes.POINTER(ctypes.c_int32)
        function = library.unicode_match_u8
        function.argtypes = [byte,ctypes.c_int,byte]+[ctypes.c_int]*4+[integer,integer]
        function.restype = ctypes.c_int
        q = np.ascontiguousarray(queries)
        bank = np.ascontiguousarray(candidates)
        ids,errors = np.empty(len(q),np.int32),np.empty(len(q),np.int32)
        status = function(q.ctypes.data_as(byte),len(q),bank.ctypes.data_as(byte),len(bank),q.shape[1],
                          query_batch,candidate_batch,ids.ctypes.data_as(integer),errors.ctypes.data_as(integer))
        if status:
            raise RuntimeError(library.unicode_match_error().decode(errors="replace"))
        if np.any(errors<0) or np.any(ids<0) or np.any(ids>=len(bank)):
            raise RuntimeError("Native search returned invalid distances or indices")
        scores = (errors.astype(np.float64)/(65025*q.shape[1])).astype(np.float32)
        return ids.astype(np.int64),scores,"native-int8"
    finally:
        for directory in directories:
            directory.close()
