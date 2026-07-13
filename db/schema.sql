CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS entidades (
  id serial PRIMARY KEY,
  slug text UNIQUE NOT NULL,
  nombre text NOT NULL,
  sigla text,
  sitio_web text
);

CREATE TABLE IF NOT EXISTS tramites (
  id integer PRIMARY KEY,
  nombre text NOT NULL,
  slug text,
  sinonimos text[] NOT NULL DEFAULT '{}',
  descripcion text,
  resultado text,
  marco_legal text,
  entidad_id integer REFERENCES entidades(id),
  costo_monto numeric,
  costo_moneda text,
  costo_concepto text,
  costo_es_gratuito boolean NOT NULL DEFAULT false,
  requisitos jsonb NOT NULL DEFAULT '[]',
  documentos jsonb NOT NULL DEFAULT '[]',
  ubicaciones jsonb NOT NULL DEFAULT '[]',
  modalidades jsonb NOT NULL DEFAULT '[]',
  enlaces jsonb NOT NULL DEFAULT '[]',
  canal text,
  digitalizado boolean NOT NULL DEFAULT false,
  embedding vector(1024),
  last_updated date
);

CREATE TABLE IF NOT EXISTS categorias (
  id serial PRIMARY KEY,
  slug text UNIQUE NOT NULL,
  nombre text NOT NULL
);

CREATE TABLE IF NOT EXISTS tramites_categorias (
  tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  categoria_id integer REFERENCES categorias(id) ON DELETE CASCADE,
  PRIMARY KEY (tramite_id, categoria_id)
);

CREATE TABLE IF NOT EXISTS eventos_de_vida (
  id serial PRIMARY KEY,
  nombre text UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS tramites_eventos (
  tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  evento_id integer REFERENCES eventos_de_vida(id) ON DELETE CASCADE,
  PRIMARY KEY (tramite_id, evento_id)
);

CREATE INDEX IF NOT EXISTS idx_tramites_embedding
  ON tramites USING hnsw (embedding vector_cosine_ops);
