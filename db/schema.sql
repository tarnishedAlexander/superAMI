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

CREATE TABLE IF NOT EXISTS consultas_log (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  conversation_id text,
  mensaje text NOT NULL,
  consulta_acumulada text,
  filtros jsonb,
  top_ids integer[],
  top_distancias real[],
  veredicto text,
  respuesta_tipo text
);

ALTER TABLE tramites ADD COLUMN IF NOT EXISTS activo boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS sync_state (
  id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  last_sync date,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fetch_cache (
  url text PRIMARY KEY,
  datos jsonb NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversaciones (
  id text PRIMARY KEY,
  mensajes jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tramites_relacionados (
  tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  related_tramite_id integer REFERENCES tramites(id) ON DELETE CASCADE,
  tipo_relacion text NOT NULL CHECK (tipo_relacion IN ('siguiente_paso', 'requisito_previo', 'alternativa', 'mismo_evento')),
  PRIMARY KEY (tramite_id, related_tramite_id)
);

-- Dominio MVP: impuestos, empresas y trámites municipales (catastro incluido).
-- Única fuente de verdad del alcance del asistente. Las columnas por_* explican
-- por qué cada trámite pertenece (auditable con un SELECT). Vista simple sobre
-- tramites: queda correcta sola tras cada sync semanal, sin ganchos en ingest.
-- Nota: sin filtro por activo — vigencia y pertenencia son ejes independientes
-- (el retrieval ya filtra t.activo); la auditoría debe ver también inactivos.
CREATE OR REPLACE VIEW dominio_mvp AS
WITH marcas AS (
  SELECT t.id AS tramite_id,
    EXISTS (
      SELECT 1 FROM tramites_categorias tc
      JOIN categorias c ON c.id = tc.categoria_id
      WHERE tc.tramite_id = t.id AND c.slug IN ('impuestos', 'empresas')
    ) AS por_categoria,
    EXISTS (
      -- por patrón y no por ids/slugs fijos: los ids son serial por instancia,
      -- y una lista de slugs queda obsoleta en silencio cuando el sync trae un
      -- GAM nuevo. Hoy matchea exactamente las 9 entidades municipales/RUAT.
      SELECT 1 FROM entidades e
      WHERE e.id = t.entidad_id AND e.nombre ILIKE '%municipal%'
    ) AS por_entidad,
    (t.nombre ILIKE '%catastr%'
     OR t.descripcion ILIKE '%catastr%'
     OR array_to_string(t.sinonimos, ' ') ILIKE '%catastr%') AS por_keyword
  FROM tramites t
)
SELECT tramite_id, por_categoria, por_entidad, por_keyword
FROM marcas
WHERE por_categoria OR por_entidad OR por_keyword;

-- Multi-fuente: distinguir el origen de cada trámite (tramites-bo/gob.bo vs lapaz.bo).
-- gob.bo trae ids provistos por la fuente (1002–3537); La Paz se scrapea y no tiene
-- id nativo, así que se le asignan ids desde 1,000,000 (estables por slug, idempotente).
ALTER TABLE tramites ADD COLUMN IF NOT EXISTS fuente text NOT NULL DEFAULT 'gob_bo';

CREATE SEQUENCE IF NOT EXISTS lapaz_id_seq START 1000000;
CREATE TABLE IF NOT EXISTS lapaz_slug_ids (
  slug text PRIMARY KEY,
  tramite_id integer NOT NULL DEFAULT nextval('lapaz_id_seq')
);
