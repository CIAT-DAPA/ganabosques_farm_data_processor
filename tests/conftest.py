import os
from pathlib import Path

import pytest
import mongomock
from mongoengine import connect, disconnect


# Entorno mínimo para evitar fallos en import-time (ej. main.py en CI).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_WORKSPACE = _REPO_ROOT / ".pytest_workspace"
(_TEST_WORKSPACE / "farm" / "outputs").mkdir(parents=True, exist_ok=True)
(_TEST_WORKSPACE / "data").mkdir(parents=True, exist_ok=True)

os.environ.setdefault("WORKSPACE", str(_TEST_WORKSPACE))
os.environ.setdefault("DATA", str(_TEST_WORKSPACE / "data"))
os.environ.setdefault("MONGO_URI", "mongodb://localhost")
os.environ.setdefault("MONGO_DB_NAME", "testdb")
os.environ.setdefault("URL_GEO", "http://localhost")
os.environ.setdefault("GEO_WORKSPACE", "ws")
os.environ.setdefault("GEO_STORE", "store")
os.environ.setdefault("GEO_USER", "user")
os.environ.setdefault("GEO_PWD", "pwd")

@pytest.fixture(scope="function", autouse=True)
def mongo_mock():
    disconnect()
    connect('testdb', host='mongodb://localhost', mongo_client_class=mongomock.MongoClient)
    yield
    disconnect()
