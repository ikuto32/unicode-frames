import numpy as np
import pytest

from badapple_unicode.search import exact_search


@pytest.mark.parametrize("backend", ["numpy", "faiss"])
def test_l2_matches_direct_exhaustive_reference(backend):
    if backend == "faiss":
        pytest.importorskip("faiss")
    rng = np.random.default_rng(42)
    queries = rng.integers(0, 256, (11, 24), dtype=np.uint8)
    candidates = rng.integers(0, 256, (37, 24), dtype=np.uint8)
    q, c = queries.astype(np.float64)/255, candidates.astype(np.float64)/255
    reference = ((q[:, None, :] - c[None, :, :])**2).mean(2)
    ids, scores, used = exact_search(queries, candidates, backend=backend, query_batch=3,
                                    candidate_batch=7, progress=False)
    np.testing.assert_array_equal(ids, reference.argmin(1))
    np.testing.assert_allclose(scores, reference.min(1), atol=3e-7)
    assert used == backend


def test_soft_iou_handles_blurred_masks_and_empty_union():
    queries = np.array([[0, 0, 0], [0.25, 0.8, 0.5]], dtype=np.float32)
    candidates = np.array([[0, 0, 0], [0.2, 0.7, 0.5], [1, 1, 1]], dtype=np.float32)
    ids, scores, _ = exact_search(queries, candidates, metric="iou", backend="numpy",
                                  candidate_batch=1, progress=False)
    np.testing.assert_array_equal(ids, [0, 1])
    np.testing.assert_allclose(scores, [0, 1-1.4/1.55], atol=1e-6)


def test_tie_is_first_candidate_across_chunks():
    ids, _, _ = exact_search(np.zeros((1, 4), dtype=np.uint8), np.ones((4, 4), dtype=np.uint8),
                             backend="numpy", candidate_batch=1, progress=False)
    assert ids.tolist() == [0]


@pytest.mark.parametrize("kwargs", [{"candidate_batch": 0}, {"query_batch": 0}, {"metric": "cosine"},
                                    {"metric": "iou", "backend": "faiss"}])
def test_invalid_search_options(kwargs):
    with pytest.raises(ValueError):
        exact_search(np.zeros((1, 3)), np.zeros((2, 3)), **kwargs)
