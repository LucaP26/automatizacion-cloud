from tests.conftest import automation_payload as _automation_payload


def test_create_automation(client, auth_headers):
    response = client.post("/automations", json=_automation_payload(), headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test Automation"
    assert body["is_active"] is True
    assert body["id"] is not None


def test_create_automation_requires_auth(client):
    response = client.post("/automations", json=_automation_payload())

    assert response.status_code == 401


def test_create_automation_rejects_invalid_cron(client, auth_headers):
    response = client.post(
        "/automations",
        json=_automation_payload(cron_expression="no soy un cron"),
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_list_automations(client, auth_headers):
    client.post("/automations", json=_automation_payload(name="A"), headers=auth_headers)
    client.post("/automations", json=_automation_payload(name="B"), headers=auth_headers)

    response = client.get("/automations", headers=auth_headers)

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["A", "B"]


def test_get_automation_not_found(client, auth_headers):
    response = client.get("/automations/99999", headers=auth_headers)

    assert response.status_code == 404


def test_update_automation_partial(client, auth_headers):
    created = client.post(
        "/automations", json=_automation_payload(), headers=auth_headers
    ).json()

    response = client.patch(
        f"/automations/{created['id']}", json={"is_active": False}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is False
    assert body["name"] == "Test Automation"  # no se tocó, no se mandó en el payload


def test_delete_automation(client, auth_headers):
    created = client.post(
        "/automations", json=_automation_payload(), headers=auth_headers
    ).json()

    delete_response = client.delete(f"/automations/{created['id']}", headers=auth_headers)
    get_response = client.get(f"/automations/{created['id']}", headers=auth_headers)

    assert delete_response.status_code == 204
    assert get_response.status_code == 404


def test_run_automation_success(client, auth_headers):
    created = client.post(
        "/automations",
        json=_automation_payload(script_code="print('hola desde el test')"),
        headers=auth_headers,
    ).json()

    response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert "hola desde el test" in body["output_log"]
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


def test_run_automation_failure(client, auth_headers):
    created = client.post(
        "/automations",
        json=_automation_payload(script_code="raise ValueError('boom')"),
        headers=auth_headers,
    ).json()

    response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert "ValueError" in body["output_log"]
    assert "boom" in body["output_log"]


def test_list_executions_for_automation(client, auth_headers):
    created = client.post(
        "/automations", json=_automation_payload(), headers=auth_headers
    ).json()

    client.post(f"/automations/{created['id']}/run", headers=auth_headers)
    client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    response = client.get(f"/automations/{created['id']}/executions", headers=auth_headers)

    assert response.status_code == 200
    executions = response.json()
    assert len(executions) == 2
    assert all(e["automation_id"] == created["id"] for e in executions)
