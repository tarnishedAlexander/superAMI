import os
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ami:ami@localhost:5433/ami")


@contextmanager
def get_connection():
    with psycopg.connect(DATABASE_URL) as conn:
        yield conn
