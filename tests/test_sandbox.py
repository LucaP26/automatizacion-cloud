import tempfile
from pathlib import Path


def _automation_payload(user_id: int, **overrides):
    payload = {
        "user_id": user_id,
        "name": "Sandbox test",
        "script_code": "print('hola')",
        "cron_expression": "0 7 * * *",
    }
    payload.update(overrides)
    return payload


def test_reserved_env_var_names_are_rejected(client, test_user):
    payload = _automation_payload(test_user.id, secrets={"PATH": "/algo/raro"})

    response = client.post("/automations", json=payload)

    assert response.status_code == 422


def test_secrets_never_appear_in_the_api_response(client, test_user):
    payload = _automation_payload(
        test_user.id, secrets={"API_KEY": "super-secreta-no-deberia-salir"}
    )

    response = client.post("/automations", json=payload)
    body = response.json()

    assert response.status_code == 201
    assert "secrets" not in body
    assert "secrets_encrypted" not in body
    assert "super-secreta-no-deberia-salir" not in response.text

    get_response = client.get(f"/automations/{body['id']}")
    assert "super-secreta-no-deberia-salir" not in get_response.text


def test_secrets_are_injected_as_env_vars_in_the_container(client, test_user):
    created = client.post(
        "/automations",
        json=_automation_payload(
            test_user.id,
            script_code="import os; print(os.environ.get('MY_SECRET', 'NO-LLEGO'))",
            secrets={"MY_SECRET": "valor-secreto-123"},
        ),
    ).json()

    response = client.post(f"/automations/{created['id']}/run")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert "valor-secreto-123" in body["output_log"]


def test_script_cannot_access_unmounted_host_files(client, test_user):
    with tempfile.TemporaryDirectory() as host_secret_dir:
        host_secret_path = Path(host_secret_dir) / "secreto_del_host.txt"
        host_secret_path.write_text("contenido-secreto-del-host", encoding="utf-8")

        script = (
            "import pathlib\n"
            "try:\n"
            f"    print(pathlib.Path(r'{host_secret_path}').read_text())\n"
            "except Exception as e:\n"
            "    print('no se pudo leer:', type(e).__name__)\n"
        )

        created = client.post(
            "/automations",
            json=_automation_payload(test_user.id, script_code=script),
        ).json()

        response = client.post(f"/automations/{created['id']}/run")

        body = response.json()
        assert "contenido-secreto-del-host" not in body["output_log"]
        assert "no se pudo leer" in body["output_log"]


def test_script_cannot_write_to_its_own_mounted_script_file(client, test_user):
    # A diferencia del test anterior (que prueba que no ve archivos del
    # host que nunca se montaron), este prueba especificamente el flag
    # read_only=True: el propio archivo que SI esta montado (/sandbox/script.py)
    # tiene que ser de solo lectura.
    script = (
        "try:\n"
        "    open('/sandbox/script.py', 'w').write('hack')\n"
        "    print('ESCRIBIO -- no deberia poder')\n"
        "except OSError as e:\n"
        "    print('escritura bloqueada:', e)\n"
    )

    created = client.post(
        "/automations",
        json=_automation_payload(test_user.id, script_code=script),
    ).json()

    response = client.post(f"/automations/{created['id']}/run")

    body = response.json()
    assert "ESCRIBIO" not in body["output_log"]
    assert "escritura bloqueada" in body["output_log"]
