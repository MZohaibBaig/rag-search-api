"""Embedding memory settings, and a regression test on real peak memory."""
import sys
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from app import embeddings


def test_model_is_built_with_one_thread_and_the_arena_off(monkeypatch):
    text_embedding = MagicMock()
    monkeypatch.setattr(embeddings, "TextEmbedding", text_embedding)
    monkeypatch.setattr(embeddings, "_model", None)

    embeddings.get_embedding_model()
    embeddings.get_embedding_model()

    text_embedding.assert_called_once()                    # lazy, and loaded only once
    assert text_embedding.call_args.kwargs["threads"] == 1
    assert text_embedding.call_args.kwargs["enable_cpu_mem_arena"] is False


def test_embed_batch_uses_batch_size_16(monkeypatch):
    model = MagicMock()
    model.embed.return_value = iter([np.zeros(384), np.zeros(384)])
    monkeypatch.setattr(embeddings, "_model", model)

    result = embeddings.embed_batch(["a", "b"])

    model.embed.assert_called_once_with(["a", "b"], batch_size=16)
    assert len(result) == 2 and len(result[0]) == 384


def _rss_mb() -> int:
    from app.rag import rss_mb
    return rss_mb()


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc for RSS")
def test_embedding_300_chunks_stays_under_300mb_of_growth():
    chunks = [f"Chunk {i}. " + ("The lighthouse keeper logged the weather and the tides. " * 9)[:490]
              for i in range(300)]
    assert all(len(c) >= 495 for c in chunks)

    embeddings.get_embedding_model()
    embeddings.embed_batch(chunks[:2])                      # warm up: first run allocates the session buffers
    baseline = _rss_mb()
    peak = baseline
    done = threading.Event()

    def sample():
        nonlocal peak
        while not done.is_set():
            peak = max(peak, _rss_mb())
            time.sleep(0.01)

    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        vectors = embeddings.embed_batch(chunks)
    finally:
        done.set()
        sampler.join()

    assert len(vectors) == 300
    growth = peak - baseline
    print(f"baseline {baseline} MB, peak {peak} MB, growth {growth} MB")
    assert growth < 300, f"RSS grew {growth} MB while embedding 300 chunks"
