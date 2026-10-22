import pytest

from tests.conftest import automation_payload


def test_register_new_user(client):
    response = client.post(
        "/auth/register", json={"email": "nuevo@example.com", "password": "password123"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "nuevo@example.com"
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_rejects_duplicate_email(client):
    client.post("/auth/register", json={"email": "repe@example.com", "password": "password123"})

    response = client.post(
        "/auth/register", json={"email": "repe@example.com", "password": "otra-password"}
    )

    assert response.status_code == 409


def test_register_rejects_short_password(client):
    response = client.post(
        "/auth/register", json={"email": "corta@example.com", "password": "123"}
    )

    assert response.status_code == 422


def test_register_rejects_password_over_72_utf8_bytes(client):
    # "ñ" pesa 2 bytes en UTF-8: 72 caracteres de "ñ" son 144 bytes, mas
    # de lo que bcrypt acepta. max_length en caracteres no alcanza para
    # evitar esto -- tiene que medirse en bytes.
    response = client.post(
        "/auth/register",
        json={"email": "larga@example.com", "password": "ñ" * 72},
    )

    assert response.status_code == 422


def test_login_against_legacy_user_without_real_hash_fails_cleanly(client, db_session):
    # Usuarios creados en fases anteriores a la 4 tienen hashed_password=''
    # (placeholder de la migracion, ver 0a51fd085625). Intentar loguearse
    # con ese usuario no debe explotar con un 500 -- bcrypt.checkpw tira
    # ValueError con un hash que no es bcrypt valido, y eso tiene que
    # traducirse a un 401 limpio, no a un error sin manejar.
    from app.models import User

    legacy_user = User(email="legacy@example.com", hashed_password="")
    db_session.add(legacy_user)
    db_session.commit()

    response = client.post(
        "/auth/login", data={"username": "legacy@example.com", "password": "cualquier-cosa"}
    )

    assert response.status_code == 401


def test_login_with_correct_credentials_returns_token(client):
    client.post(
        "/auth/register", json={"email": "login@example.com", "password": "password123"}
    )

    response = client.post(
        "/auth/login", data={"username": "login@example.com", "password": "password123"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_with_wrong_password_fails(client):
    client.post(
        "/auth/register", json={"email": "login2@example.com", "password": "password123"}
    )

    response = client.post(
        "/auth/login", data={"username": "login2@example.com", "password": "mal"}
    )

    assert response.status_code == 401


def test_login_with_unknown_email_still_runs_password_check(client):
    # No alcanza con que devuelva 401 -- tiene que gastar el mismo tiempo
    # (correr bcrypt.checkpw igual) que con un email que SI existe, para
    # no filtrar por timing que ese email no esta registrado. Se verifica
    # mockeando verify_password y confirmando que se llamo, en vez de
    # medir tiempos reales (que seria un test lento y poco confiable).
    from unittest.mock import patch

    with patch("app.routers.auth.verify_password") as mock_verify:
        mock_verify.return_value = False
        client.post(
            "/auth/login",
            data={"username": "no-existe-de-verdad@example.com", "password": "lo-que-sea"},
        )

    mock_verify.assert_called_once()


def test_login_with_unknown_email_fails(client):
    response = client.post(
        "/auth/login", data={"username": "no-existe@example.com", "password": "password123"}
    )

    assert response.status_code == 401


def test_accessing_automations_without_token_is_rejected(client):
    response = client.get("/automations")

    assert response.status_code == 401


def test_accessing_automations_with_garbage_token_is_rejected(client):
    response = client.get(
        "/automations", headers={"Authorization": "Bearer esto-no-es-un-token-valido"}
    )

    assert response.status_code == 401


def test_full_register_then_login_then_use_token_flow(client):
    client.post(
        "/auth/register", json={"email": "flujo@example.com", "password": "password123"}
    )
    login_response = client.post(
        "/auth/login", data={"username": "flujo@example.com", "password": "password123"}
    )
    token = login_response.json()["access_token"]

    response = client.get("/automations", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "method,path_suffix",
    [("get", ""), ("patch", ""), ("delete", ""), ("post", "/run")],
    ids=["see", "update", "delete", "run"],
)
def test_user_cannot_act_on_another_users_automation(
    client, auth_headers, other_user_auth_headers, method, path_suffix
):
    # Un solo test parametrizado en vez de 4 copias casi idénticas -- las
    # cuatro verifican la misma invariante (404 al tocar lo de otro
    # usuario), difieren solo en el verbo/ruta.
    created = client.post(
        "/automations",
        json=automation_payload(name="De otro usuario"),
        headers=other_user_auth_headers,
    ).json()

    kwargs = {"headers": auth_headers}
    if method == "patch":
        kwargs["json"] = {"is_active": False}

    response = getattr(client, method)(
        f"/automations/{created['id']}{path_suffix}", **kwargs
    )

    assert response.status_code == 404


def test_list_automations_only_shows_own(client, auth_headers, other_user_auth_headers):
    client.post("/automations", json=automation_payload(name="Mia"), headers=auth_headers)
    client.post(
        "/automations", json=automation_payload(name="De otro"), headers=other_user_auth_headers
    )

    response = client.get("/automations", headers=auth_headers)

    names = [a["name"] for a in response.json()]
    assert names == ["Mia"]
