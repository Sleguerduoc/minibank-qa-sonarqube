"""Persistencia SQLite y preparación del entorno de laboratorio."""

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

VERSION = "3.2.0"
BUILD = "3.2.0"
RUT_DEMO = "111111111"
CLAVE_DEMO = "1234"  # Credencial pública del dataset ficticio (README); en la DB solo se guarda su hash.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
SALDOS = {"estandar": 2_000_000, "saldo_bajo": 500_000}
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "minibank_v3.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    rut TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    clave TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    user_rut TEXT PRIMARY KEY REFERENCES users(rut) ON DELETE CASCADE,
    saldo INTEGER NOT NULL CHECK (typeof(saldo) = 'integer' AND saldo >= 0),
    transferido_hoy INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(transferido_hoy) = 'integer' AND transferido_hoy >= 0)
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_rut TEXT NOT NULL REFERENCES users(rut) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_rut TEXT NOT NULL REFERENCES users(rut) ON DELETE CASCADE,
    rut TEXT NOT NULL,
    nombre TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_key TEXT NOT NULL,
    UNIQUE(user_rut, rut),
    UNIQUE(user_rut, alias_key)
);
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_rut TEXT NOT NULL REFERENCES users(rut) ON DELETE CASCADE,
    comprobante TEXT NOT NULL UNIQUE,
    fecha TEXT NOT NULL,
    destinatario TEXT NOT NULL,
    monto INTEGER NOT NULL CHECK (monto > 0),
    comision INTEGER NOT NULL CHECK (comision >= 0),
    saldo_resultante INTEGER NOT NULL CHECK (saldo_resultante >= 0)
);
CREATE TABLE IF NOT EXISTS lab_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

REQUIRED_COLUMNS = {
    "users": {"rut", "nombre", "clave"},
    "accounts": {"user_rut", "saldo", "transferido_hoy"},
    "sessions": {"token", "user_rut", "created_at"},
    "recipients": {"id", "user_rut", "rut", "nombre", "alias", "alias_key"},
    "movements": {"id", "user_rut", "comprobante", "fecha", "destinatario",
                  "monto", "comision", "saldo_resultante"},
    "lab_meta": {"key", "value"},
}


def hash_clave(clave: str, salt: bytes | None = None) -> str:
    """Hash adaptativo (scrypt) con sal aleatoria: formato scrypt$<sal_hex>$<hash_hex>."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(clave.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verificar_clave(clave: str, almacenada: str) -> bool:
    try:
        esquema, salt_hex, digest_hex = almacenada.split("$")
    except ValueError:
        return False
    if esquema != "scrypt":
        return False
    calculado = hashlib.scrypt(clave.encode("utf-8"), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(calculado.hex(), digest_hex)


def get_db_path() -> Path:
    return Path(os.environ.get("MINIBANK_DB_PATH", str(DEFAULT_DB_PATH))).resolve()


def get_connection(create: bool = False) -> sqlite3.Connection:
    path = get_db_path()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    else:
        conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                               timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def transaction(write: bool = False):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_profile(perfil: str):
    if perfil not in SALDOS:
        raise ValueError("Perfil inválido: estandar o saldo_bajo")


def _seed(conn, perfil: str):
    conn.execute("INSERT OR IGNORE INTO users(rut, nombre, clave) VALUES (?, ?, ?)",
                 (RUT_DEMO, "Camila Rojas", hash_clave(CLAVE_DEMO)))
    # Migración: bases creadas por builds anteriores guardaban la clave en texto plano.
    for row in conn.execute("SELECT rut, clave FROM users").fetchall():
        if not row[1].startswith("scrypt$"):
            conn.execute("UPDATE users SET clave = ? WHERE rut = ?", (hash_clave(row[1]), row[0]))
    conn.execute("INSERT OR IGNORE INTO accounts(user_rut, saldo, transferido_hoy) VALUES (?, ?, 0)",
                 (RUT_DEMO, SALDOS[perfil]))
    conn.execute("INSERT OR IGNORE INTO lab_meta(key, value) VALUES ('perfil', ?)", (perfil,))


def initialize_database(perfil: str = "estandar"):
    validate_profile(perfil)
    conn = get_connection(create=True)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        _seed(conn, perfil)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_database(perfil: str = "estandar"):
    validate_profile(perfil)
    with transaction(write=True) as conn:
        _seed(conn, perfil)


def invalidate_sessions():
    with transaction(write=True) as conn:
        conn.execute("DELETE FROM sessions")


def reset_database(perfil: str = "estandar"):
    validate_profile(perfil)
    with transaction(write=True) as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM recipients")
        conn.execute("DELETE FROM movements")
        conn.execute("DELETE FROM accounts")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM lab_meta")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('recipients', 'movements')")
        _seed(conn, perfil)


def inspect_database():
    result = {"status": "not_ready", "version": VERSION, "build": BUILD,
              "database": "sqlite", "schema": "error", "seed": "missing", "perfil": None}
    try:
        with transaction() as conn:
            for table, columns in REQUIRED_COLUMNS.items():
                actual = {row["name"] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}
                if not columns <= actual:
                    return result
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return result
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                return result
            result["schema"] = "ready"
            profile = conn.execute("SELECT value FROM lab_meta WHERE key = 'perfil'").fetchone()
            result["perfil"] = profile[0] if profile else None
            user = conn.execute(
                "SELECT u.nombre, u.clave, a.saldo, a.transferido_hoy FROM users u "
                "JOIN accounts a ON a.user_rut = u.rut WHERE u.rut = ?", (RUT_DEMO,)
            ).fetchone()
            if (user is not None and user["nombre"] == "Camila Rojas" and verificar_clave(CLAVE_DEMO, user["clave"])
                    and isinstance(user["saldo"], int) and user["saldo"] >= 0
                    and isinstance(user["transferido_hoy"], int) and user["transferido_hoy"] >= 0
                    and result["perfil"] in SALDOS):
                result["seed"] = "loaded"
                result["status"] = "ready"
    except (sqlite3.Error, OSError, ValueError):
        pass
    return result
