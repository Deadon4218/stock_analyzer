import os
from groq import Groq

_client: Groq | None = None


def get_groq() -> Groq:
    """Returns a singleton Groq client instance."""
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client
