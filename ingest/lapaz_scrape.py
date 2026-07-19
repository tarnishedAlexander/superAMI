"""Ingesta de trámites del GAMLP desde lapaz.bo (2ª fuente, fuente='lapaz_gamlp').

lapaz.bo es WordPress sin API pública -> se scrapea el HTML. Los archivos de
categoría (/blog/category/<slug>/) están deshabilitados (404) y los posts no
exponen su categoría, así que la enumeración sale del sitemap plano: se listan
los ~259 posts /blog/<slug>/ y se clasifica CADA uno por su CONTENIDO:
  - ¿es trámite? -> tiene las secciones típicas ("Requisitos", "Dónde se inicia",
    "Pasos del ciudadano", "En qué consiste"). Las noticias no las tienen.
  - ¿es del dominio MVP? -> su texto matchea uno de los grupos de palabras clave
    (impuestos / catastro / negocios / vehículos); ese grupo da la categoría.
Solo los posts que pasan ambos filtros se extraen con el modelo potente (prompt en
ingest.lapaz_mapper) y se guardan reutilizando guardar_tramite_completo.

Idempotente y resumible: salta los slugs ya cargados. Pensado para correr detached.

Uso:
    python -m ingest.lapaz_scrape --limit 3        # prueba chica
    python -m ingest.lapaz_scrape                   # todo el dominio MVP
"""
import argparse
import logging
import os
import re
import time
import unicodedata
import urllib.request

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from db.connection import get_connection
from db.queries import guardar_tramite_completo
from ingest.lapaz_mapper import SISTEMA_EXTRACCION, mapear_extraccion, schema_extraccion
from ingest.mapper import texto_para_embedding
from providers import factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SITEMAP = "https://lapaz.bo/sitemap.xml"
UA = "Mozilla/5.0 (compatible; AMI-ingest/1.0)"
POST_RE = re.compile(r"/blog/[^/]+/?$")
PAUSA = float(os.environ.get("PAUSA_SEGUNDOS", "0.8"))

# Secciones típicas de una ficha de trámite (normalizadas sin tildes). Una noticia
# no las tiene: >=2 presentes => es un trámite.
MARCADORES = ("requisito", "donde se inicia", "pasos del ciudadano",
              "en que consiste", "no tiene ningun costo", "objeto del tramite")

# Grupos del dominio MVP: (slug_categoria, nombre, regex de palabras clave). Primer
# grupo que matchea el texto del post gana y da la categoría. Si ninguno matchea,
# el trámite queda fuera del MVP y se descarta.
GRUPOS_MVP = (
    ("lapaz-catastro-y-territorio", "Catastro y Territorio",
     re.compile(r"catastr|uso de suelo|linea (?:y|de) nivel|urbaniz|predio|manzano|parcela|territori|plano", re.I)),
    ("lapaz-vehiculos", "Vehículos",
     re.compile(r"vehiculo|automotor|empadronamiento|radicatoria|\bplaca|motorizad|transporte", re.I)),
    ("lapaz-negocios-y-comercio", "Negocios y Comercio",
     re.compile(r"licencia de funcionamiento|actividad(?:es)? economic|comercio|negocio|publicidad|mercado", re.I)),
    ("lapaz-impuestos", "Impuestos",
     re.compile(r"impuesto|tributo|tributar|transferencia|rc-?iva|patente", re.I)),
)


def _norm(s: str) -> str:
    """minúsculas sin tildes, para matchear marcadores de forma robusta."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def es_tramite(texto: str) -> bool:
    t = _norm(texto)
    return sum(m in t for m in MARCADORES) >= 2


def categoria_de(texto: str) -> dict | None:
    """Primer grupo MVP cuyo regex matchea el texto -> categoría; None si ninguno."""
    for slug, nombre, rx in GRUPOS_MVP:
        if rx.search(texto):
            return {"slug": slug, "nombre": nombre}
    return None


def _get(url: str, timeout: int = 30) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        logger.warning("fetch falló %s: %s", url, str(e)[:80])
        return None


def _slug(url: str) -> str:
    return url.rstrip("/").split("/blog/")[-1]


def descubrir_posts() -> list[str]:
    """URLs /blog/<slug>/ del sitemap, con los trámites (priority 0.64) adelante."""
    xml = _get(SITEMAP) or ""
    posts: list[tuple[float, str]] = []
    for bloque in re.findall(r"<url>(.*?)</url>", xml, re.S):
        m = re.search(r"<loc>(.*?)</loc>", bloque)
        if not m:
            continue
        loc = m.group(1)
        if "/blog/" not in loc or "/category/" in loc or "/page/" in loc or not POST_RE.search(loc):
            continue
        p = re.search(r"<priority>(.*?)</priority>", bloque)
        prio = float(p.group(1)) if p else 0.0
        posts.append((prio, loc))
    # trámites primero: priority 0.64 arriba, luego el resto por priority desc
    posts.sort(key=lambda x: (x[0] != 0.64, -x[0]))
    return [loc for _, loc in posts]


def texto_del_post(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "form", "aside"]):
        t.decompose()
    cont = soup.select_one(".entry-content, article, main") or soup.body or soup
    txt = cont.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", txt)[:8000]


def _resolver_id(conn, slug: str) -> int:
    return conn.execute(
        "INSERT INTO lapaz_slug_ids (slug) VALUES (%s) "
        "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING tramite_id",
        (slug,),
    ).fetchone()[0]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="máximo de trámites nuevos a cargar")
    parser.add_argument("--pausa", type=float, default=None, help="segundos entre requests")
    args = parser.parse_args()
    global PAUSA
    if args.pausa is not None:
        PAUSA = args.pausa

    chat = factory.chat_potente()
    emb = factory.embedder()

    with get_connection() as conn:
        cargados = {
            r[0] for r in conn.execute(
                "SELECT l.slug FROM lapaz_slug_ids l JOIN tramites t ON t.id = l.tramite_id"
            ).fetchall()
        }
    posts = descubrir_posts()
    logger.info("sitemap: %d posts | ya cargados: %d slugs de La Paz", len(posts), len(cargados))

    guardados = fallos = noticias = fuera_mvp = 0
    for url in posts:
        slug = _slug(url)
        if slug in cargados:
            continue
        html = _get(url)
        if not html:
            fallos += 1
            continue
        texto = texto_del_post(html)
        if not es_tramite(texto):        # noticia u otra página, no una ficha
            noticias += 1
            time.sleep(PAUSA)
            continue
        categoria = categoria_de(texto)
        if categoria is None:            # trámite, pero fuera del dominio MVP
            fuera_mvp += 1
            time.sleep(PAUSA)
            continue
        datos = chat.complete_json(
            system=SISTEMA_EXTRACCION,
            messages=[{"role": "user", "content": texto}],
            schema=schema_extraccion(),
            max_tokens=4000,
        )
        if not datos or not datos.get("nombre"):
            logger.warning("extracción vacía para %s", slug)
            fallos += 1
            time.sleep(PAUSA)
            continue
        try:
            with get_connection() as conn:
                tid = _resolver_id(conn, slug)
                conn.commit()
            fila = mapear_extraccion(datos, tid, slug, url, categoria)
            vector = emb.embed_documents([texto_para_embedding(fila)])[0]
            with get_connection() as conn:
                guardar_tramite_completo(conn, fila, vector)
                conn.commit()
            cargados.add(slug)
            guardados += 1
            logger.info("guardado [%d] %s (%s) -> %s", tid, slug[:50], fila["nombre"][:45], categoria["slug"])
        except Exception:
            logger.exception("falló guardando %s", slug)
            fallos += 1
        if args.limit and guardados >= args.limit:
            break
        time.sleep(PAUSA)

    logger.info("listo: %d guardados, %d fallos, %d noticias, %d fuera-MVP",
                guardados, fallos, noticias, fuera_mvp)


if __name__ == "__main__":
    main()
