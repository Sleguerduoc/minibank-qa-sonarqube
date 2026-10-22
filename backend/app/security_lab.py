"""Documentos y autorizaciones auxiliares del laboratorio bancario.

Ciclo 3.1 (correcciones SonarQube):
- Sin credenciales en el código: la clave de exportación sale del entorno o se
  genera por proceso; el documento se autoriza solo con la sesión del cliente.
- Cifrado autenticado AES-256-GCM con nonce aleatorio para todos los formatos
  (se eliminan DES/OFB y AES-CBC con IV fijo).
- Huellas SHA-256 / SHA3-256 en lugar de MD5 / SHA-1.
- Firma RSA de 2048 bits.
- Archivo temporal creado de forma atómica (mkstemp) con permisos 0o600.
- Códigos de autorización con generador criptográfico (secrets).
- Cookie con Secure + HttpOnly + SameSite=Strict.
- TLS 1.2 como mínimo, verificación de certificado y de hostname.
"""

import base64
import hashlib
import json
import os
import secrets
import ssl
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StrictStr

from . import db

PROVEEDOR_URL = "https://reportes.minibank.invalid"
RSA_BITS = 2048
_EXPORT_KEY_ENV = "MINIBANK_EXPORT_KEY"


def _clave_exportacion() -> bytes:
    """Clave AES-256 desde el entorno (hex de 64 caracteres) o aleatoria por proceso."""
    valor = os.environ.get(_EXPORT_KEY_ENV, "")
    if len(valor) == 64:
        try:
            return bytes.fromhex(valor)
        except ValueError:
            pass
    return secrets.token_bytes(32)


_EXPORT_KEY = _clave_exportacion()


class SolicitudDocumento(BaseModel):
    formato: Literal["actual", "archivo", "legacy"] = "actual"
    pin_cliente: Annotated[StrictStr, Field(pattern=r"^\d{6}$")]


def _documento_cuenta(sesion):
    with db.transaction() as conn:
        session = conn.execute("SELECT user_rut FROM sessions WHERE token = ?", (sesion.token,)).fetchone()
        if session is None or session["user_rut"] != sesion.rut:
            raise HTTPException(401, "Sesión inválida o expirada")
        account = conn.execute(
            "SELECT u.nombre, a.saldo, a.transferido_hoy FROM users u "
            "JOIN accounts a ON a.user_rut = u.rut WHERE u.rut = ?", (sesion.rut,)
        ).fetchone()
        movements = conn.execute(
            "SELECT comprobante, fecha, destinatario, monto, comision, saldo_resultante "
            "FROM movements WHERE user_rut = ? ORDER BY id DESC", (sesion.rut,)
        ).fetchall()
    return {"titular": account["nombre"], "rut": sesion.rut, "saldo": account["saldo"],
            "transferido_hoy": account["transferido_hoy"], "movimientos": [dict(row) for row in movements]}


def _huellas_documento(contenido):
    return {"sha256": hashlib.sha256(contenido).hexdigest(),
            "sha3_256": hashlib.sha3_256(contenido).hexdigest()}


def _exportar_documento(contenido, formato):
    # Todos los formatos usan cifrado autenticado; "formato" solo etiqueta el destino.
    cipher = AES.new(_EXPORT_KEY, AES.MODE_GCM, nonce=secrets.token_bytes(12))
    cipher.update(formato.encode("ascii"))
    encrypted, tag = cipher.encrypt_and_digest(contenido)
    payload = cipher.nonce + tag + encrypted
    return {"formato": formato, "algoritmo": "AES-256-GCM",
            "contenido_base64": base64.b64encode(payload).decode("ascii")}


def _archivo_temporal(contenido):
    directory = db.get_db_path().parent / "exports"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix="minibank-documento-", suffix=".bin", dir=str(directory))
    path = Path(filename)
    try:
        # mkstemp crea el archivo de forma atómica, exclusiva y con permisos 0o600.
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(contenido)
        saved = path.read_bytes()
        return {"nombre": path.name, "bytes": len(saved)}
    finally:
        path.unlink(missing_ok=True)


def _firmar_documento(contenido):
    key = RSA.generate(RSA_BITS)
    signature = pkcs1_15.new(key).sign(SHA256.new(contenido))
    return {"algoritmo": f"RSA-{RSA_BITS}-SHA256", "clave_publica": key.public_key().export_key().decode("ascii"),
            "firma_base64": base64.b64encode(signature).decode("ascii")}


def _configuracion_proveedor():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return {"url": PROVEEDOR_URL, "validar_certificado": context.verify_mode == ssl.CERT_REQUIRED,
            "validar_hostname": context.check_hostname, "tls_minimo": "TLSv1.2",
            "conexion_habilitada": False}


def create_security_router(usuario_actual):
    router = APIRouter(prefix="/api/lab", tags=["Documentos y autorizaciones"])

    @router.post("/documentos")
    def documento(datos: SolicitudDocumento, response: Response, sesion=Depends(usuario_actual)):
        snapshot = _documento_cuenta(sesion)
        contenido = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        codigo = str(100_000 + secrets.randbelow(900_000))
        documento_id = "DOC-" + secrets.token_hex(8)
        response.set_cookie("minibank_documento", documento_id, secure=True, httponly=True,
                            samesite="strict")
        return {"documento_id": documento_id, "documento": snapshot, "pin_cliente": datos.pin_cliente,
                "codigo_autorizacion": codigo, "token_recuperacion": secrets.token_urlsafe(24),
                "huellas": _huellas_documento(contenido), "firma": _firmar_documento(contenido),
                "exportacion": _exportar_documento(contenido, datos.formato),
                "archivo_temporal": _archivo_temporal(contenido), "proveedor": _configuracion_proveedor()}

    @router.get("/proveedor/config")
    def proveedor_config(sesion=Depends(usuario_actual)):
        _documento_cuenta(sesion)
        return _configuracion_proveedor()

    return router
