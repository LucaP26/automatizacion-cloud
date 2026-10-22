import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import User
from app.services.security import create_access_token, hash_password

# Opcional en Settings (los containers de producción no la necesitan), pero
# obligatoria para la suite: nunca caer en silencio a la base de desarrollo.
assert settings.test_database_url, "Falta TEST_DATABASE_URL en el entorno/.env para correr los tests"
test_engine = create_engine(settings.test_database_url)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="session", autouse=True)
def _create_test_schema():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db_session():
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def test_user(db_session):
    user = User(email="tester@example.com", hashed_password=hash_password("password123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def other_user(db_session):
    user = User(email="otro@example.com", hashed_password=hash_password("password123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def auth_headers(test_user):
    # El token se genera directo (no vía POST /auth/login) porque estos
    # fixtures son para los tests de CRUD de automations, no de auth en sí
    # -- el login en sí ya tiene sus propios tests en tests/test_auth.py.
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def other_user_auth_headers(other_user):
    token = create_access_token(other_user.id)
    return {"Authorization": f"Bearer {token}"}


def automation_payload(**overrides):
    # Compartido entre test_automation.py, test_sandbox.py, test_notifier.py
    # y test_auth.py -- antes cada archivo tenía su propia copia de esto.
    payload = {
        "name": "Test Automation",
        "script_code": "print('Hello, World!')",
        "cron_expression": "0 7 * * *",
    }
    payload.update(overrides)
    return payload
