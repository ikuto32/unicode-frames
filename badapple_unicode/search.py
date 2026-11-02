"""Exact nearest-neighbor search with bounded score matrices."""
from __future__ import annotations

import importlib.util

import numpy as np


def select_backend(requested: str, metric: str) -> str:
    if requested == "auto":
        if importlib.util.find_spec("torch"):
            import torch
            if torch.cuda.is_available():
                return "cuda"
        if metric == "l2" and importlib.util.find_spec("faiss"):
            return "faiss"
        return "numpy"
    if requested == "faiss" and metric != "l2":
        raise ValueError("FAISS IndexFlatL2 supports --metric l2; use numpy/cuda for soft IoU")
    return requested


def exact_search(queries: np.ndarray, candidates: np.ndarray, *, metric: str = "l2",
                 backend: str = "auto", query_batch: int = 128, candidate_batch: int = 8192,
                 progress: bool = True) -> tuple[np.ndarray, np.ndarray, str]:
    """Return candidate IDs and mean squared error or 1 - fuzzy IoU.

    IoU uses sum(min(q,g))/sum(max(q,g)) on the blurred coverage masks.
    L2 is ||q||² + ||g||² - 2qgᵀ. All candidates are visited; no ANN pruning.
    uint8 inputs represent [0,255], floating inputs represent [0,1].
    """
    if metric not in {"l2", "iou"}:
        raise ValueError(metric)
    if query_batch < 1 or candidate_batch < 1:
        raise ValueError("Batch sizes must be positive")
    if queries.ndim != 2 or candidates.ndim != 2 or queries.shape[1] != candidates.shape[1]:
        raise ValueError("Queries and candidates must be 2D with equal feature width")
    if len(candidates) == 0:
        raise ValueError("Empty dictionary")
    if not np.isfinite(queries).all():
        raise ValueError("Non-finite queries")
    backend = select_backend(backend, metric)
    q = queries.astype(np.float32) / (255 if queries.dtype == np.uint8 else 1)
    best_ids = np.zeros(len(q), dtype=np.int64)
    best_scores = np.full(len(q), np.inf, dtype=np.float32)
    dimension = candidates.shape[1]

    if backend == "faiss":
        import faiss
        # Flat is exhaustive. Bounded add chunks avoid a second full float32 copy.
        faiss.omp_set_num_threads(min(16, faiss.omp_get_max_threads()))
        index = faiss.IndexFlatL2(dimension)
        for start in range(0, len(candidates), candidate_batch):
            part = np.asarray(candidates[start:start + candidate_batch], dtype=np.float32)
            if candidates.dtype == np.uint8:
                part /= 255
            index.add(part)
        for start in range(0, len(q), query_batch):
            scores, ids = index.search(q[start:start + query_batch], 1)
            best_ids[start:start + len(ids)] = ids[:, 0]
            best_scores[start:start + len(ids)] = np.maximum(scores[:, 0], 0) / dimension
            if progress:
                print(f"match ({backend}): {min(start+query_batch, len(q))}/{len(q)}", flush=True)
        return best_ids, best_scores, backend

    if backend == "cuda":
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA backend requested but torch.cuda.is_available() is false")
        # IEEE float32; no TF32 or half precision that might change close winners.
        torch.backends.cuda.matmul.allow_tf32 = False
        qt = torch.from_numpy(q).to("cuda")
        result_scores = torch.full((len(q),), float("inf"), device="cuda")
        result_ids = torch.zeros(len(q), dtype=torch.int64, device="cuda")
        for cs in range(0, len(candidates), candidate_batch):
            chunk = np.asarray(candidates[cs:cs + candidate_batch], dtype=np.float32)
            if candidates.dtype == np.uint8:
                chunk /= 255
            ct = torch.from_numpy(chunk).to("cuda")
            cn = (ct * ct).sum(1)
            for qs in range(0, len(q), query_batch):
                query = qt[qs:qs + query_batch]
                if metric == "l2":
                    scores = ((query * query).sum(1)[:, None] + cn[None, :] - 2 * query @ ct.T).clamp_min_(0) / dimension
                else:
                    # Sum |q-g|, then min=(sum(q)+sum(g)-L1)/2; max=(...+L1)/2.
                    l1 = torch.cdist(query, ct, p=1)
                    sums = query.sum(1)[:, None] + ct.sum(1)[None, :]
                    scores = torch.where(sums + l1 > 0, 2 * l1 / (sums + l1).clamp_min(1e-12), 0)
                values, ids = scores.min(1)
                old = result_scores[qs:qs + len(query)]
                improve = values < old
                result_scores[qs:qs + len(query)] = torch.where(improve, values, old)
                result_ids[qs:qs + len(query)] = torch.where(improve, ids + cs, result_ids[qs:qs + len(query)])
            if progress:
                print(f"match (cuda): candidates {min(cs+candidate_batch,len(candidates))}/{len(candidates)}", flush=True)
        return result_ids.cpu().numpy(), result_scores.cpu().numpy(), backend

    if backend != "numpy":
        raise ValueError(f"Unknown backend: {backend}")
    for cs in range(0, len(candidates), candidate_batch):
        chunk = np.asarray(candidates[cs:cs + candidate_batch], dtype=np.float32)
        if candidates.dtype == np.uint8:
            chunk /= 255
        cn = np.einsum("ij,ij->i", chunk, chunk)
        for qs in range(0, len(q), query_batch):
            query = q[qs:qs + query_batch]
            if metric == "l2":
                scores = (np.einsum("ij,ij->i", query, query)[:, None] + cn[None, :] - 2 * query @ chunk.T)
                scores = np.maximum(scores, 0) / dimension
            else:
                # Bound the temporary Q×G×D tensor, including for user-selected batch sizes.
                scores = np.empty((len(query), len(chunk)), dtype=np.float32)
                for qi, vector in enumerate(query):
                    intersection = np.minimum(vector, chunk).sum(1)
                    union = np.maximum(vector, chunk).sum(1)
                    scores[qi] = 1 - np.divide(intersection, union, out=np.ones_like(union), where=union > 0)
            local = scores.argmin(1)
            values = scores[np.arange(len(query)), local]
            old = best_scores[qs:qs + len(query)]
            improve = values < old
            best_ids[qs:qs + len(query)][improve] = cs + local[improve]
            old[improve] = values[improve]
        if progress:
            print(f"match (numpy): candidates {min(cs+candidate_batch,len(candidates))}/{len(candidates)}", flush=True)
    return best_ids, best_scores, backend
