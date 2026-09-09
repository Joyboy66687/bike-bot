import pytest
from db import init_db


@pytest.fixture
def db_conn():
    conn = init_db(":memory:")
    yield conn
    conn.close()
