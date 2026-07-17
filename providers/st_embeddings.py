class SentenceTransformersEmbeddingProvider:
    """Embeddings locales (dependencia opcional: pip install -r requirements-oss.txt).

    Los modelos e5 REQUIEREN los prefijos "query:"/"passage:" — sin ellos la calidad
    de retrieval se degrada silenciosamente.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-base", model=None):
        if model is None:
            from sentence_transformers import SentenceTransformer  # import perezoso: torch es pesado

            model = SentenceTransformer(model_name)
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectores = self._model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True)
        return [list(map(float, v)) for v in vectores]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode([f"query: {text}"], normalize_embeddings=True)[0]
        return list(map(float, vector))
