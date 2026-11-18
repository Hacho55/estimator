"""Corpus sintético del CAG para la segunda corrida del stress test (D26).

Deterministas y con semilla: N estimaciones históricas con la forma de ``app/context/examples.py``.
Los distractores son de sectores ajenos a la consulta. La aguja es casi idéntica al proyecto
consultado y lleva un dato exclusivo (``NEEDLE_TOKEN`` en el título y una integración con nombre
propio) que no aparece en ningún otro sitio: si la respuesta lo nombra, el modelo la encontró.

El corpus empieza por las estimaciones reales vigentes (las de ``examples.py``) y se completa con
distractores. ``nonce`` cambia una frase del primer ejemplo para que el prefijo no pueda estar ya
en la caché del proveedor (muestra fría, D28) sin cambiar nada que importe.
"""

from __future__ import annotations

import random
from typing import Literal

from app.context.examples import ESTIMATION_EXAMPLES

Position = Literal["start", "middle", "end"]

NEEDLE_TOKEN = "Arrecife"
NEEDLE_ID = "estiba-arrecife-2025"
SEED = 6

QUERY_TRANSCRIPT = (
    "Somos el operador de una terminal de contenedores. Necesitamos una aplicación web y móvil para "
    "planificar los turnos de los estibadores: asignación por grúa y por buque, intercambio de turnos "
    "entre trabajadores, control de horas según el convenio de estiba y avisos de cambios por el móvil. "
    "Debe integrarse con nuestro sistema operativo de terminal (TOS) para leer las escalas de buques. "
    "Indica en los supuestos qué proyecto histórico de referencia usas y por qué."
)

_SECTORS = {
    "salud": ("historia clínica", "citas de fisioterapia", "telemedicina", "farmacia hospitalaria"),
    "educacion": ("campus virtual", "matrícula online", "tutorías", "evaluación continua"),
    "retail": ("fidelización", "catálogo omnicanal", "click and collect", "inventario de tienda"),
    "banca": ("onboarding digital", "scoring de riesgo", "gestión de tarjetas", "banca empresas"),
    "turismo": ("reservas de hotel", "guías turísticas", "check-in online", "gestión de excursiones"),
    "energia": ("lectura de contadores", "autoconsumo solar", "incidencias de red", "facturación eléctrica"),
    "agro": ("trazabilidad de cosecha", "riego inteligente", "cooperativa agrícola", "control de plagas"),
    "legal": ("gestión de expedientes", "firma de contratos", "agenda de juzgados", "minutas"),
    "seguros": ("partes de siniestro", "tarificador de pólizas", "red de peritos", "renovaciones"),
    "inmobiliaria": ("portal de alquiler", "visitas virtuales", "gestión de comunidades", "tasaciones"),
    "deporte": ("reservas de pistas", "socios del club", "ligas amateur", "entrenamiento personal"),
    "medios": ("hemeroteca digital", "suscripciones", "podcast", "gestión de anunciantes"),
}
_PRODUCTS = ("Plataforma web de", "App móvil de", "Portal de", "Sistema de", "Panel interno de")
_TASKS = (
    ("Diseño de pantallas", "Diseño", "ux_ui"),
    ("API principal", "Backend", "backend"),
    ("Modelo de datos", "Backend", "backend"),
    ("Integración externa", "Backend", "backend"),
    ("Interfaz web", "Frontend", "frontend"),
    ("App móvil", "Frontend", "frontend"),
    ("Roles y permisos", "Backend", "backend"),
    ("Informes", "Frontend", "frontend"),
    ("Despliegue y CI", "Operaciones", "devops"),
    ("Pruebas", "Calidad", "qa"),
    ("Coordinación", "Gestión", "pm"),
)
_ASSUMPTIONS = (
    "Un solo idioma en la primera fase.",
    "El cliente aporta los datos iniciales en CSV.",
    "Sin migración de datos históricos.",
    "La autenticación usa el proveedor corporativo existente.",
    "El diseño gráfico parte de una guía de estilo existente.",
    "Hosting en la nube del cliente.",
)
_RISKS = (
    "El alcance de los informes puede crecer.",
    "La integración externa no tiene entorno de pruebas.",
    "Los usuarios finales no están disponibles para validar.",
    "Dependencia de un proveedor con plazos lentos.",
    "Requisitos legales aún sin cerrar.",
)


def _distractor(index: int, rng: random.Random) -> dict:
    sector = list(_SECTORS)[index % len(_SECTORS)]
    topic = _SECTORS[sector][(index // len(_SECTORS)) % len(_SECTORS[sector])]
    product = _PRODUCTS[index % len(_PRODUCTS)]
    tasks = rng.sample(_TASKS, rng.randint(5, 7))
    return {
        "id": f"syn-{index:03d}",
        "title": f"{product} {topic} ({sector} {index:03d})",
        "sector": sector,
        "year": rng.choice((2023, 2024, 2025)),
        "client_profile": f"cliente del sector {sector}, {rng.randint(2, 40) * 10} usuarios",
        "meeting_summary": (
            f"El cliente quiere {product.lower()} {topic}, con acceso por roles y un panel de seguimiento. "
            f"Prioriza la primera versión en {rng.randint(2, 6)} meses."
        ),
        "tasks": [{"name": name, "phase": phase, "role": role, "quantity": rng.randint(2, 16) * 5, "unit": "hours"} for name, phase, role in tasks],
        "team": [
            {"role": "fullstack", "count": rng.randint(1, 3), "dedication": 1},
            {"role": "qa", "count": 1, "dedication": 0.5},
        ],
        "duration_weeks_min": (weeks := rng.randint(4, 14)),
        "duration_weeks_max": weeks + rng.randint(1, 4),
        "assumptions": rng.sample(_ASSUMPTIONS, 3),
        "risks": rng.sample(_RISKS, 2),
    }


def needle(total_hours: int) -> dict:
    """La estimación pertinente. Las horas se reparten para sumar ``total_hours`` (múltiplo de 5)."""
    shares = (
        ("Diseño de cuadrantes y app", "Diseño", "ux_ui", 0.10),
        ("Motor de asignación por grúa y buque", "Backend", "backend", 0.25),
        ("Integración con el TOS Navis N4", "Backend", "backend", 0.15),
        ("Control de horas por convenio de estiba", "Backend", "backend", 0.15),
        ("App móvil de turnos y avisos", "Frontend", "frontend", 0.15),
        ("Panel web de planificación", "Frontend", "frontend", 0.10),
        ("Pruebas", "Calidad", "qa", 0.10),
    )
    tasks = [
        {"name": name, "phase": phase, "role": role, "quantity": max(5, round(total_hours * share / 5) * 5), "unit": "hours"}
        for name, phase, role, share in shares
    ]
    tasks[1]["quantity"] += total_hours - sum(task["quantity"] for task in tasks)  # Cuadra el total.
    return {
        "id": NEEDLE_ID,
        "title": f"Turnos de estiba {NEEDLE_TOKEN}",
        "sector": "logistica_portuaria",
        "year": 2025,
        "client_profile": "terminal de contenedores, 600 estibadores",
        "meeting_summary": (
            "Planificación de turnos de estibadores por grúa y buque, intercambio de turnos, control de "
            "horas según el convenio de estiba y avisos al móvil, integrada con el TOS Navis N4."
        ),
        "tasks": tasks,
        "team": [
            {"role": "backend", "count": 2, "dedication": 1},
            {"role": "frontend", "count": 1, "dedication": 1},
            {"role": "qa", "count": 1, "dedication": 0.5},
        ],
        "duration_weeks_min": 14,
        "duration_weeks_max": 18,
        "assumptions": ["El TOS expone las escalas por API.", "El convenio vigente es el de 2024."],
        "risks": ["Los cambios de escala de última hora obligan a replanificar en minutos."],
    }


def corpus(size: int, *, needle_hours: int | None, position: Position = "middle", nonce: str = "") -> list[dict]:
    """``size`` estimaciones en total, contando la aguja si ``needle_hours`` no es None.

    Los distractores son siempre los mismos para un ``size`` dado: el control sin aguja y las
    tres posiciones comparten corpus y solo cambia dónde está la aguja (o si está).
    """
    if size < 1:
        raise ValueError("size debe ser positivo")
    rng = random.Random(SEED)
    real = [dict(item) for item in ESTIMATION_EXAMPLES if item.get("year", 0) >= 2022]
    pool = real + [_distractor(index, rng) for index in range(max(0, size))]
    others = pool[: size - (1 if needle_hours is not None else 0)]
    items = list(others)
    if needle_hours is not None:
        slot = {"start": 0, "middle": len(others) // 2, "end": len(others)}[position]
        items.insert(slot, needle(needle_hours))
    if nonce and items:
        first = dict(items[0])
        first["client_profile"] = f"{first['client_profile']} (ref. {nonce})"
        items[0] = first
    return items
