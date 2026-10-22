from unittest.mock import patch

from app.config import settings


def _automation_payload(**overrides):
    payload = {
        "name": "Notifier test",
        "script_code": "print(1)",
        "cron_expression": "0 7 * * *",
    }
    payload.update(overrides)
    return payload


def test_notifies_webhook_on_failed_execution(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "notification_webhook_url", "http://fake.test/webhook")

    created = client.post(
        "/automations",
        json=_automation_payload(script_code="raise ValueError('boom')"),
        headers=auth_headers,
    ).json()

    with patch("app.services.notifier.requests.post") as mock_post:
        response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.json()["status"] == "failed"
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://fake.test/webhook"
    assert "boom" in kwargs["json"]["text"]


def test_does_not_notify_on_successful_execution(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "notification_webhook_url", "http://fake.test/webhook")

    created = client.post(
        "/automations", json=_automation_payload(), headers=auth_headers
    ).json()

    with patch("app.services.notifier.requests.post") as mock_post:
        response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.json()["status"] == "success"
    mock_post.assert_not_called()


def test_secret_values_are_redacted_before_notifying(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "notification_webhook_url", "http://fake.test/webhook")

    created = client.post(
        "/automations",
        json=_automation_payload(
            script_code=(
                "import os; print(os.environ['MY_SECRET']); raise ValueError('boom')"
            ),
            secrets={"MY_SECRET": "valor-secreto-no-deberia-salir"},
        ),
        headers=auth_headers,
    ).json()

    with patch("app.services.notifier.requests.post") as mock_post:
        response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.json()["status"] == "failed"
    assert "valor-secreto-no-deberia-salir" in response.json()["output_log"]  # en la API si

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert "valor-secreto-no-deberia-salir" not in kwargs["json"]["text"]  # al webhook, no
    assert "REDACTED" in kwargs["json"]["text"]


def test_does_not_notify_when_webhook_not_configured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "notification_webhook_url", None)

    created = client.post(
        "/automations",
        json=_automation_payload(script_code="raise ValueError('boom')"),
        headers=auth_headers,
    ).json()

    with patch("app.services.notifier.requests.post") as mock_post:
        response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.json()["status"] == "failed"
    mock_post.assert_not_called()
