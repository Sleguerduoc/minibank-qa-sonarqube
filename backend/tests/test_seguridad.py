"""Pruebas de comportamiento de las correcciones de seguridad (ciclos 3.1 y 3.2)."""

import base64
import logging
import stat

import pytest
from fastapi.testclient import TestClient

from app import db, security_lab
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIBANK_DB_PATH", str(tmp_path / "seg.db"))
    with TestClient(app) as current:
        yield current


def autenticar(client):
    r = client.post("/api/login", json={"rut": "11.111.111-1", "clave": "1234"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def pedir_documento(client, headers, formato="actual"):
    return client.post("/api/lab/documentos", headers=headers, json={"formato": formato, "pin_cliente": "123456"})


# ---------------- Ciclo 3.1 ----------------

@pytest.mark.parametrize("formato", ["actual", "archivo", "legacy"])
def test_documento_cifrado_autenticado_en_todos_los_formatos(client, formato):
    r = pedir_documento(client, autenticar(client), formato)
    assert r.status_code == 200
    exp = r.json()["exportacion"]
    assert exp["algoritmo"] == "AES-256-GCM"
    payload = base64.b64decode(exp["contenido_base64"])
    assert len(payload) > 28  # nonce (12) + tag (16) + datos


def test_nonce_distinto_en_cada_exportacion(client):
    h = autenticar(client)
    a = base64.b64decode(pedir_documento(client, h, "archivo").json()["exportacion"]["contenido_base64"])
    b = base64.b64decode(pedir_documento(client, h, "archivo").json()["exportacion"]["contenido_base64"])
    assert a[:12] != b[:12]


def test_documento_no_requiere_ni_acepta_clave_compartida(client):
    assert not hasattr(security_lab, "DOC_SERVICE_PASSWORD")
    assert not hasattr(security_lab, "EXPORT_PASSWORD")
    assert pedir_documento(client, autenticar(client)).status_code == 200


def test_documento_sin_sesion_responde_401(client):
    assert pedir_documento(client, {}).status_code == 401


def test_huellas_y_firma_robustas(client):
    data = pedir_documento(client, autenticar(client)).json()
    assert set(data["huellas"]) == {"sha256", "sha3_256"}
    assert data["firma"]["algoritmo"] == "RSA-2048-SHA256"


def test_cookie_segura(client):
    r = pedir_documento(client, autenticar(client))
    cookie = r.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_proveedor_tls_verificado(client):
    cfg = client.get("/api/lab/proveedor/config", headers=autenticar(client)).json()
    assert cfg["url"].startswith("https://")
    assert cfg["validar_certificado"] is True and cfg["validar_hostname"] is True
    assert cfg["tls_minimo"] == "TLSv1.2"


def test_archivo_temporal_privado(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIBANK_DB_PATH", str(tmp_path / "x.db"))
    vistos = []
    original = security_lab.os.fdopen

    def espia(fd, *a, **k):
        vistos.append(stat.S_IMODE(security_lab.os.fstat(fd).st_mode))
        return original(fd, *a, **k)

    monkeypatch.setattr(security_lab.os, "fdopen", espia)
    info = security_lab._archivo_temporal(b"abc")
    assert info["bytes"] == 3 and vistos == [0o600]


def test_login_rechazado_no_registra_la_clave(client, caplog):
    logger = logging.getLogger("minibank.lab")
    logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="minibank.lab"):
            client.post("/api/login", json={"rut": "11.111.111-1", "clave": "CENTINELA-9981"})
    finally:
        logger.propagate = False
    assert "Login rechazado" in caplog.text
    assert "CENTINELA-9981" not in caplog.text


# ---------------- Ciclo 3.2 ----------------

def test_clave_almacenada_con_hash(client):
    import sqlite3
    with sqlite3.connect(db.get_db_path()) as conn:
        clave = conn.execute("SELECT clave FROM users").fetchone()[0]
    assert clave.startswith("scrypt$") and "1234" not in clave
    assert db.verificar_clave("1234", clave) and not db.verificar_clave("12345", clave)


def test_migra_clave_en_texto_plano(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("MINIBANK_DB_PATH", str(tmp_path / "vieja.db"))
    db.initialize_database()
    with sqlite3.connect(db.get_db_path()) as conn:
        conn.execute("UPDATE users SET clave = '1234'")
    db.initialize_database()
    assert db.inspect_database()["status"] == "ready"


def test_rechazo_por_saldo_no_modifica_acumulado(client):
    client.post("/api/lab/reset?perfil=saldo_bajo")
    h = autenticar(client)
    r = client.post("/api/transferir", headers=h, json={"destinatario": "12.345.678-5", "monto": 499_800})
    assert r.status_code == 400 and r.json()["codigo"] == "SALDO_INSUFICIENTE"
    cuenta = client.get("/api/cuenta", headers=h).json()
    assert cuenta["saldo"] == 500_000 and cuenta["transferido_hoy"] == 0


def test_alias_se_guarda_sin_espacios_exteriores(client):
    h = autenticar(client)
    r = client.post("/api/destinatarios", headers=h, json={"rut": "12.345.678-5", "nombre": "Ana", "alias": "  Casa  "})
    assert r.status_code == 201 and r.json()["destinatario"]["alias"] == "Casa"
    assert client.get("/api/destinatarios", headers=h).json()["destinatarios"][0]["alias"] == "Casa"
