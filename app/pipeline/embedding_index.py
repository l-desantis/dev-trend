import numpy as np


class EmbeddingIndex:
    """Brute-force cosine similarity over an in-memory matrix.

    Suitable for <10k vectors. Recompute on each pipeline run.
    """

    def __init__(self, ids: list[int], vectors: list[list[float]]) -> None:
        self._ids = np.asarray(ids, dtype=np.int64)
        if len(ids) == 0:
            self._matrix = np.empty((0, 0), dtype=np.float32)
            return
        m = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        self._matrix = m / np.where(norms == 0, 1, norms)

    def nearest(
        self, query: list[float], k: int = 1, threshold: float = 0.0
    ) -> list[tuple[int, float]]:
        if len(self._ids) == 0:
            return []
        q = np.asarray(query, dtype=np.float32)
        norm = np.linalg.norm(q)
        qn = q / (norm if norm != 0 else 1.0)
        sims = self._matrix @ qn
        idx = np.argsort(-sims)[:k]
        return [(int(self._ids[i]), float(sims[i])) for i in idx if sims[i] >= threshold]

    def __len__(self) -> int:
        return len(self._ids)
