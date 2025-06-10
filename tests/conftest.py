import pytest
import mongomock
from mongoengine import connect, disconnect

@pytest.fixture(scope="function", autouse=True)
def mongo_mock():
    disconnect()
    connect('testdb', host='mongodb://localhost', mongo_client_class=mongomock.MongoClient)
    yield
    disconnect()
