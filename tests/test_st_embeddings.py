from providers.st_embeddings import SentenceTransformersEmbeddingProvider


class FakeSTModel:
    """Evita depender de torch en la suite: el modelo real es dependencia opcional."""

    def __init__(self):
        self.llamadas = []

    def encode(self, texts, normalize_embeddings=False):
        self.llamadas.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_prefijos_e5_y_conversion_a_float():
    fake = FakeSTModel()
    provider = SentenceTransformersEmbeddingProvider(model=fake)
    docs = provider.embed_documents(["hola", "chau"])
    assert docs == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.llamadas[0] == ["passage: hola", "passage: chau"]

    q = provider.embed_query("carnet")
    assert q == [0.1, 0.2, 0.3]
    assert fake.llamadas[1] == ["query: carnet"]
