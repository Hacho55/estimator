"""BBDD del negocio: usuarios con su tier y proyectos cerrados con sus métricas reales.

El esquema es del backend. El estimador no lo conoce: solo ve lo que devuelve la API.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('developer', 'pm', 'executive'))
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    client_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'in_progress', 'cancelled')),
    technologies TEXT NOT NULL,
    scope_summary TEXT NOT NULL,
    team_size INTEGER NOT NULL,
    estimated_hours INTEGER NOT NULL,
    actual_hours INTEGER NOT NULL,
    duration_weeks INTEGER NOT NULL,
    materialized_risks TEXT NOT NULL,
    margin_pct REAL NOT NULL
);
"""

# Tres usuarios de demostración, uno por tier.
USERS = [
    ("u-dev", "Ana (ingeniería)", "developer"),
    ("u-pm", "Pablo (entrega)", "pm"),
    ("u-exec", "Elena (dirección)", "executive"),
]

# Proyectos del histórico. client_name y margin_pct son internos: nunca salen por la API.
PROJECTS = [
    ("p-101", "CRM B2B para equipo comercial", "Comercial Iberia SA", "completed", ["React", "PostgreSQL", "Node.js"],
     "CRM con contactos, embudo de oportunidades e informes semanales", 3, 520, 610, 12,
     ["Migración de datos del CRM antiguo más lenta de lo previsto"], 0.21),
    ("p-102", "Portal de reservas deportivas", "Club Delta", "completed", ["Next.js", "Stripe", "PostgreSQL"],
     "Reservas de pistas con pagos en línea, panel de administración y avisos por correo", 2, 380, 420, 9,
     ["Integración de pagos con conciliación manual"], 0.18),
    ("p-103", "Sincronización de pedidos con ERP", "Distribuciones Norte", "completed", ["Python", "RabbitMQ", "SAP"],
     "Integración por API de pedidos con un ERP, reintentos, cola asíncrona y panel de errores", 2, 300, 390, 10,
     ["Límites de la API del ERP", "Entorno de pruebas del ERP inestable"], 0.15),
    ("p-104", "Landing con HubSpot y blog", "Agencia Sur", "completed", ["Next.js", "HubSpot"],
     "Landing con formulario integrado en HubSpot y blog de noticias", 1, 110, 105, 3,
     [], 0.25),
    ("p-105", "App móvil de citas", "Clínica Vela", "completed", ["React Native", "Firebase"],
     "App móvil de reserva de citas con recordatorios push y calendario del profesional", 3, 640, 780, 14,
     ["Revisión de las tiendas de apps retrasó el lanzamiento", "Notificaciones push en iOS"], 0.12),
    ("p-106", "Panel interno de RRHH", "Grupo Atlas", "completed", ["Vue", "Django", "PostgreSQL"],
     "Panel interno con gestión de usuarios, permisos por rol, registro de auditoría e informes por correo", 2, 420, 470, 10,
     ["Permisos por rol más complejos de lo pactado"], 0.19),
    ("p-107", "Tienda online con pasarela de pagos", "Moda Ruiz", "completed", ["Shopify", "Stripe", "Node.js"],
     "Comercio electrónico con catálogo, carrito, tres pasarelas de pago y facturación", 3, 560, 690, 13,
     ["Tres pasarelas de pago con flujos distintos", "Facturación fiscal por país"], 0.14),
    ("p-108", "Plataforma de inventario multi-almacén", "Logística Norte", "completed", ["React", "Node.js", "PostgreSQL"],
     "Stock por almacén, roles de operario y supervisor, alertas de rotura y panel de métricas", 3, 480, 505, 9,
     ["Reglas de reserva de stock crecieron durante el proyecto"], 0.2),
    ("p-109", "Migración a microservicios", "Banca Este", "cancelled", ["Kubernetes", "Go", "Kafka"],
     "Partir un monolito en microservicios con Kafka", 6, 2400, 1100, 20,
     ["Cancelado por cambio de prioridades"], 0.0),
]


@contextmanager
def connect(path: str):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(path: str) -> None:
    """Crea el esquema y siembra los datos de demostración si la BBDD está vacía."""
    with connect(path) as db:
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            db.executemany("INSERT INTO users VALUES (?, ?, ?)", USERS)
        if db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
            db.executemany(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (pid, title, client, status, json.dumps(tech), scope, team, est, act, weeks, json.dumps(risks), margin)
                    for pid, title, client, status, tech, scope, team, est, act, weeks, risks, margin in PROJECTS
                ],
            )


def get_user(path: str, user_id: str) -> sqlite3.Row | None:
    with connect(path) as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(path: str) -> list[sqlite3.Row]:
    with connect(path) as db:
        return db.execute("SELECT * FROM users ORDER BY id").fetchall()


def set_tier(path: str, user_id: str, tier: str) -> sqlite3.Row | None:
    """Cambia el tier y devuelve la fila resultante en la misma transacción."""
    with connect(path) as db:
        return db.execute("UPDATE users SET tier = ? WHERE id = ? RETURNING *", (tier, user_id)).fetchone()


def completed_projects(path: str) -> list[dict]:
    with connect(path) as db:
        rows = db.execute("SELECT * FROM projects WHERE status = 'completed'").fetchall()
    return [
        {**dict(row), "technologies": json.loads(row["technologies"]), "materialized_risks": json.loads(row["materialized_risks"])}
        for row in rows
    ]
