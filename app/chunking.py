def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """
    Split text into overlapping chunks.
    
    Args:
        text: The text to chunk.
        chunk_size: Target chunk size in characters.
        overlap: Number of characters to overlap between chunks.
    
    Returns:
        List of text chunks.
    """
    chunks = []
    start = 0
    
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        
        if chunk:  # Only add non-empty chunks
            chunks.append(chunk)
        
        # Move start position, accounting for overlap
        start = end - overlap if end < len(text) else end
    
    return chunks