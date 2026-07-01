import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def get_groq_answer(question: str, context: str) -> str:
    """
    Send a question + retrieved context to Groq and get an answer.
    
    Args:
        question: The user's question.
        context: The relevant document chunks (concatenated).
    
    Returns:
        The LLM's generated answer.
    """
    client = Groq(api_key=GROQ_API_KEY)
    
    system_prompt = """You are a helpful assistant answering questions based on provided context.
Answer only using the context provided. If the context doesn't contain enough information to answer the question, say so.
Keep your answer concise and grounded in the context."""
    
    user_prompt = f"""Context:
{context}

Question: {question}

Answer:"""
    
    message = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=1024,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )

    return message.choices[0].message.content