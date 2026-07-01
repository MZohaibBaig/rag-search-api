from sentence_transformers import SentenceTransformer
import os

# Load model once at startup (expensive operation)
_model = None

def get_embedding_model():
    """Lazy-load and cache the embedding model."""
    global _model
    if _model is None:
        model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        _model = SentenceTransformer(model_name)
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
    embedding = model.encode(text, convert_to_tensor=False)
    return embedding.tolist()

def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts efficiently in one batch.
    
    Args:
        texts: List of texts to embed.
    
    Returns:
        List of embeddings (each a list of 384 floats).
    """
    model = get_embedding_model()
    embeddings = model.encode(texts, convert_to_tensor=False)
    return [emb.tolist() for emb in embeddings]