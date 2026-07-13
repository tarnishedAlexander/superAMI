import os

from dotenv import load_dotenv

from providers.anthropic_chat import AnthropicChatProvider
from providers.base import ChatProvider, EmbeddingProvider
from providers.openai_compat import OpenAICompatChatProvider, OpenAICompatEmbeddingProvider
from providers.voyage_embeddings import VoyageEmbeddingProvider

load_dotenv()

_NVIDIA_BASE_URL_DEFAULT = "https://integrate.api.nvidia.com/v1"


def _proveedor() -> str:
    return os.environ.get("PROVIDER", "nvidia").lower()


def _nvidia_chat(modelo: str) -> OpenAICompatChatProvider:
    return OpenAICompatChatProvider(
        model=modelo,
        base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_BASE_URL_DEFAULT),
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
    )


def chat_potente() -> ChatProvider:
    if _proveedor() == "anthropic":
        return AnthropicChatProvider(os.environ.get("MODELO_POTENTE", "claude-sonnet-5"))
    return _nvidia_chat(os.environ.get("MODELO_POTENTE", "meta/llama-3.3-70b-instruct"))


def chat_economico() -> ChatProvider:
    if _proveedor() == "anthropic":
        return AnthropicChatProvider(os.environ.get("MODELO_ECONOMICO", "claude-haiku-4-5"))
    return _nvidia_chat(os.environ.get("MODELO_ECONOMICO", "meta/llama-3.1-8b-instruct"))


def embedder() -> EmbeddingProvider:
    if _proveedor() == "anthropic":
        return VoyageEmbeddingProvider(
            model=os.environ.get("MODELO_EMBEDDINGS", "voyage-4-lite"),
            output_dimension=int(os.environ.get("EMBEDDING_DIM", "1024")),
        )
    return OpenAICompatEmbeddingProvider(
        model=os.environ.get("MODELO_EMBEDDINGS", "baai/bge-m3"),
        base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_BASE_URL_DEFAULT),
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
    )
