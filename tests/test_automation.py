def _automation_payload(user_id: int, **overrides):
    payload = {
        "user_id": user_id,
        "name": "Test Automation",
        "script_code": "print('Hello, World!')",
        "cron_expression": "0 7 * * *",
    }
    payload.update(overrides)
    return payload


def test_create_automation(client, test_user):
    payload = _automation_payload(test_user.id)

    response = client.post("/automations", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test Automation"
    assert body["is_active"] is True
    assert body["id"] is not None


def test_create_automation_rejects_invalid_cron(client, test_user):
    payload = _automation_payload(test_user.id, cron_expression="no soy un cron")

    response = client.post("/automations", json=payload)

    assert response.status_code == 422


def test_list_automations(client, test_user):
    client.post("/automations", json=_automation_payload(test_user.id, name="A"))
    client.post("/automations", json=_automation_payload(test_user.id, name="B"))

    response = client.get("/automations")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["A", "B"]


def test_get_automation_not_found(client):
    response = client.get("/automations/99999")

    assert response.status_code == 404


def test_update_automation_partial(client, test_user):
    created = client.post("/automations", json=_automation_payload(test_user.id)).json()

    response = client.patch(f"/automations/{created['id']}", json={"is_active": False})

    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is False
    assert body["name"] == "Test Automation"  # no se tocó, no se mandó en el payload


def test_delete_automation(client, test_user):
    created = client.post("/automations", json=_automation_payload(test_user.id)).json()

    delete_response = client.delete(f"/automations/{created['id']}")
    get_response = client.get(f"/automations/{created['id']}")

    assert delete_response.status_code == 204
    assert get_response.status_code == 404


def test_run_automation_success(client, test_user):
    created = client.post(
        "/automations",
        json=_automation_payload(test_user.id, script_code="print('hola desde el test')"),
    ).json()

    response = client.post(f"/automations/{created['id']}/run")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert "hola desde el test" in body["output_log"]
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


def test_run_automation_failure(client, test_user):
    created = client.post(
        "/automations",
        json=_automation_payload(test_user.id, script_code="raise ValueError('boom')"),
    ).json()

    response = client.post(f"/automations/{created['id']}/run")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert "ValueError" in body["output_log"]
    assert "boom" in body["output_log"]


def test_list_executions_for_automation(client, test_user):
    created = client.post("/automations", json=_automation_payload(test_user.id)).json()

    client.post(f"/automations/{created['id']}/run")
    client.post(f"/automations/{created['id']}/run")

    response = client.get(f"/automations/{created['id']}/executions")

    assert response.status_code == 200
    executions = response.json()
    assert len(executions) == 2
    assert all(e["automation_id"] == created["id"] for e in executions)
