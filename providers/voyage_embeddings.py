import voyageai

_TAMANO_LOTE = 100


class VoyageEmbeddingProvider:
    def __init__(self, model: str = "voyage-4-lite", output_dimension: int = 512, client=None):
        self.model = model
        self.output_dimension = output_dimension
        self._client = client or voyageai.Client()  # lee VOYAGE_API_KEY del entorno

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectores: list[list[float]] = []
        for i in range(0, len(texts), _TAMANO_LOTE):
            lote = texts[i : i + _TAMANO_LOTE]
            resultado = self._client.embed(
                lote, model=self.model, input_type="document", output_dimension=self.output_dimension
            )
            vectores.extend(resultado.embeddings)
        return vectores

    def embed_query(self, text: str) -> list[float]:
        resultado = self._client.embed(
            [text], model=self.model, input_type="query", output_dimension=self.output_dimension
        )
        return resultado.embeddings[0]
