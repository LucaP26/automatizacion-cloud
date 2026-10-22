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


def test_user_cannot_see_another_users_automation(client, auth_headers, other_user_auth_headers):
    created = client.post(
        "/automations",
        json={
            "name": "De otro usuario",
            "script_code": "print(1)",
            "cron_expression": "0 7 * * *",
        },
        headers=other_user_auth_headers,
    ).json()

    response = client.get(f"/automations/{created['id']}", headers=auth_headers)

    assert response.status_code == 404


def test_user_cannot_update_another_users_automation(
    client, auth_headers, other_user_auth_headers
):
    created = client.post(
        "/automations",
        json={
            "name": "De otro usuario",
            "script_code": "print(1)",
            "cron_expression": "0 7 * * *",
        },
        headers=other_user_auth_headers,
    ).json()

    response = client.patch(
        f"/automations/{created['id']}", json={"is_active": False}, headers=auth_headers
    )

    assert response.status_code == 404


def test_user_cannot_delete_another_users_automation(
    client, auth_headers, other_user_auth_headers
):
    created = client.post(
        "/automations",
        json={
            "name": "De otro usuario",
            "script_code": "print(1)",
            "cron_expression": "0 7 * * *",
        },
        headers=other_user_auth_headers,
    ).json()

    response = client.delete(f"/automations/{created['id']}", headers=auth_headers)

    assert response.status_code == 404


def test_user_cannot_run_another_users_automation(client, auth_headers, other_user_auth_headers):
    created = client.post(
        "/automations",
        json={
            "name": "De otro usuario",
            "script_code": "print(1)",
            "cron_expression": "0 7 * * *",
        },
        headers=other_user_auth_headers,
    ).json()

    response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.status_code == 404


def test_list_automations_only_shows_own(client, auth_headers, other_user_auth_headers):
    client.post(
        "/automations",
        json={"name": "Mia", "script_code": "print(1)", "cron_expression": "0 7 * * *"},
        headers=auth_headers,
    )
    client.post(
        "/automations",
        json={"name": "De otro", "script_code": "print(1)", "cron_expression": "0 7 * * *"},
        headers=other_user_auth_headers,
    )

    response = client.get("/automations", headers=auth_headers)

    names = [a["name"] for a in response.json()]
    assert names == ["Mia"]
