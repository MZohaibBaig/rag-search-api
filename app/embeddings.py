from fastembed import TextEmbedding
import os

# Load model once at startup (expensive operation)
_model = None

# fastembed's default batch_size is 256; ONNX Runtime's memory arena then keeps that peak
# (~2.7 GB for a large upload). Small batches with the arena off keep RSS flat.
EMBED_BATCH_SIZE = 16

def get_embedding_model():
    """Lazy-load and cache the embedding model."""
    global _model
    if _model is None:
        model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        _model = TextEmbedding(
            model_name,
            cache_dir=os.getenv("FASTEMBED_CACHE_PATH", "/models"),
            threads=1,
            enable_cpu_mem_arena=False,
        )
    return _model

def embed_text(text: str) -> list[float]:
    """
    Convert text to a 384-dimensional embedding vector.
    
    Args:
        text: The text to embed.
    
    Returns:
        A list of 384 floats (the embedding).
    """
    model = get_embedding_model()
    return next(iter(model.embed([text]))).tolist()

def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts efficiently in one batch.
    
    Args:
        texts: List of texts to embed.
    
    Returns:
        List of embeddings (each a list of 384 floats).
    """
    model = get_embedding_model()
    return [emb.tolist() for emb in model.embed(texts, batch_size=EMBED_BATCH_SIZE)]
