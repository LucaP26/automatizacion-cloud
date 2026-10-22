import tempfile
from pathlib import Path

from tests.conftest import automation_payload as _automation_payload


def test_reserved_env_var_names_are_rejected(client, auth_headers):
    payload = _automation_payload(secrets={"PATH": "/algo/raro"})

    response = client.post("/automations", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_secrets_never_appear_in_the_api_response(client, auth_headers):
    payload = _automation_payload(secrets={"API_KEY": "super-secreta-no-deberia-salir"})

    response = client.post("/automations", json=payload, headers=auth_headers)
    body = response.json()

    assert response.status_code == 201
    assert "secrets" not in body
    assert "secrets_encrypted" not in body
    assert "super-secreta-no-deberia-salir" not in response.text

    get_response = client.get(f"/automations/{body['id']}", headers=auth_headers)
    assert "super-secreta-no-deberia-salir" not in get_response.text


def test_secrets_are_injected_as_env_vars_in_the_container(client, auth_headers):
    created = client.post(
        "/automations",
        json=_automation_payload(
            script_code="import os; print(os.environ.get('MY_SECRET', 'NO-LLEGO'))",
            secrets={"MY_SECRET": "valor-secreto-123"},
        ),
        headers=auth_headers,
    ).json()

    response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert "valor-secreto-123" in body["output_log"]


def test_script_cannot_access_unmounted_host_files(client, auth_headers):
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
            json=_automation_payload(script_code=script),
            headers=auth_headers,
        ).json()

        response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

        body = response.json()
        assert "contenido-secreto-del-host" not in body["output_log"]
        assert "no se pudo leer" in body["output_log"]


def test_script_cannot_write_to_its_own_mounted_script_file(client, auth_headers):
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
        json=_automation_payload(script_code=script),
        headers=auth_headers,
    ).json()

    response = client.post(f"/automations/{created['id']}/run", headers=auth_headers)

    body = response.json()
    assert "ESCRIBIO" not in body["output_log"]
    assert "escritura bloqueada" in body["output_log"]
