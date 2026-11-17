"""Conversaciones sintéticas de 20 turnos con su fact-tracker.

Cada turno declara los hechos que, desde ese turno, la memoria debería reflejar. Un hecho
con la misma ``key`` declarado más tarde **sustituye** al anterior: así se expresa un pivote
(«React ya no está en las tecnologías») o una contradicción («el presupuesto ya no es 30 k»).

``MemoryDriftMetric`` comprueba los hechos vigentes en el turno N contra el snapshot de
``GET /sessions/{id}``. Se evalúan también en el mismo turno en que se declaran: así se
distingue «nunca se capturó» de «se capturó y se perdió».

Perfiles (bloque 2 del ejercicio):

- ``growing``: cada turno añade un requisito coherente. ¿Sobrevive el nombre del turno 1 al
  20? ¿Aguanta la memoria (tope de 20 elementos por lista) 20 turnos de alcance creciente?
- ``pivot``: el turno 5 cambia de React a Flutter. ¿Se actualiza limpio o se acumulan?
- ``contradiction``: el turno 3 dice 30 k€ y el 8 dice 80 k€. ¿Cuál se queda?
- ``burst``: un único turno inicial, idéntico, para lanzarlo en paralelo (ráfagas).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Where = Literal["project_name", "team", "technologies", "rejected", "constraints", "scope", "memory", "answer"]
Expect = Literal["present", "absent"]


@dataclass(frozen=True)
class Fact:
    """Un hecho a recordar. ``variants``: formas aceptadas (sin distinguir mayúsculas)."""

    key: str
    variants: tuple[str, ...]
    where: Where = "memory"
    expect: Expect = "present"

    def __post_init__(self) -> None:
        if not self.variants or not all(item.strip() for item in self.variants):
            raise ValueError(f"hecho {self.key!r} sin variantes")


@dataclass(frozen=True)
class ScenarioTurn:
    transcript: str
    facts: tuple[Fact, ...] = ()


@dataclass(frozen=True)
class Scenario:
    name: str
    project_type: str
    detail_level: str
    turns: tuple[ScenarioTurn, ...] = field(default_factory=tuple)

    def active_facts(self, turn_index: int) -> list[Fact]:
        """Hechos vigentes tras el turno ``turn_index`` (1-based): el último de cada key."""
        current: dict[str, Fact] = {}
        for turn in self.turns[:turn_index]:
            for fact in turn.facts:
                current[fact.key] = fact
        return list(current.values())


def _fact(key: str, *variants: str, where: Where = "memory", expect: Expect = "present") -> Fact:
    return Fact(key=key, variants=variants, where=where, expect=expect)


def _turns(*items: str | tuple[str, tuple[Fact, ...]]) -> tuple[ScenarioTurn, ...]:
    return tuple(ScenarioTurn(item) if isinstance(item, str) else ScenarioTurn(item[0], item[1]) for item in items)


NIMBUS = _fact("project_name", "Nimbus", where="project_name")

GROWING = Scenario(
    name="growing",
    project_type="web_saas",
    detail_level="medium",
    turns=_turns(
        (
            "Somos una distribuidora y queremos el proyecto Nimbus: una plataforma web SaaS para gestionar "
            "el inventario de nuestros almacenes. Frontend en React, backend en FastAPI con PostgreSQL. "
            "Contamos con un equipo de 4 personas.",
            (NIMBUS, _fact("tech.fastapi", "FastAPI", where="technologies"), _fact("team", "4", where="team")),
        ),
        (
            "Necesitamos autenticación con SSO corporativo (Azure AD) para los empleados.",
            (_fact("req.sso", "SSO", "Azure AD", "Entra"),),
        ),
        (
            "Debe ser multi-tenant: cada cliente de la distribuidora ve solo su stock.",
            (_fact("req.tenant", "multi-tenant", "multitenant", "multi tenant", "multiinquilino"),),
        ),
        (
            "Añade un audit log de todos los movimientos de stock, consultable por fecha y usuario.",
            (_fact("req.audit", "audit", "auditoría", "auditoria"),),
        ),
        (
            "Exportación de inventario a CSV desde cualquier listado.",
            (_fact("req.csv", "CSV"),),
        ),
        (
            "Alertas por email cuando un artículo baje del stock mínimo.",
            (_fact("req.alerts", "stock mínimo", "stock minimo", "alerta"),),
        ),
        (
            "Roles y permisos: administrador, jefe de almacén y operario.",
            (_fact("req.roles", "roles", "permisos"),),
        ),
        (
            "Lectura de códigos de barras desde el navegador con la cámara.",
            (_fact("req.barcode", "código de barras", "codigo de barras", "códigos de barras", "barcode"),),
        ),
        "¿Cuánto cambia la estimación si el equipo pasa de 4 a 5 personas?",
        (
            "Queremos una API pública REST para que los clientes integren su ERP.",
            (_fact("req.api", "API pública", "API publica", "API REST", "public API"),),
        ),
        (
            "Webhooks para notificar cambios de stock a sistemas externos.",
            (_fact("req.webhooks", "webhook"),),
        ),
        (
            "Cobro de la suscripción con Stripe, con planes mensual y anual.",
            (_fact("tech.stripe", "Stripe", where="memory"),),
        ),
        "Recuérdame qué incluye ahora mismo el alcance, en una línea.",
        (
            "Internacionalización: español, inglés y portugués.",
            (_fact("req.i18n", "i18n", "internacionaliz", "idiomas", "portugués", "portugues"),),
        ),
        (
            "Informes PDF mensuales de rotación de inventario.",
            (_fact("req.reports", "rotación", "rotacion"),),
        ),
        (
            "Búsqueda de texto completo sobre el catálogo de artículos.",
            (_fact("req.search", "búsqueda", "busqueda", "full-text", "texto completo"),),
        ),
        (
            "Un panel con métricas de rotura de stock por almacén.",
            (_fact("req.dashboard", "panel", "dashboard", "métricas", "metricas"),),
        ),
        (
            "Importación masiva de artículos desde Excel.",
            (_fact("req.import", "Excel", "importación", "importacion"),),
        ),
        "¿Qué parte del alcance concentra más riesgo?",
        "Dame la estimación final con todo lo que hemos hablado.",
    ),
)

ORION = _fact("project_name", "Orion", where="project_name")

PIVOT = Scenario(
    name="pivot",
    project_type="mobile_app",
    detail_level="medium",
    turns=_turns(
        (
            "Proyecto Orion: una app de fidelización para una cadena de cafeterías. Clientes acumulan puntos "
            "con un QR y los canjean. La haríamos como web app en React, con backend en Node.js.",
            (ORION, _fact("stack.react", "React", where="technologies"), _fact("tech.node", "Node", where="technologies")),
        ),
        (
            "Los puntos caducan a los 12 meses y hay niveles: bronce, plata y oro.",
            (_fact("req.levels", "nivel", "bronce", "oro"),),
        ),
        (
            "El panel de la cadena debe mostrar canjes por tienda y por día.",
            (_fact("req.panel", "panel", "canje"),),
        ),
        "¿Cuántas horas se van solo en el sistema de puntos?",
        (
            "Cambio importante: el cliente quiere apps nativas en iOS y Android. Abandonamos React; "
            "lo haremos en Flutter. El backend en Node.js se mantiene.",
            (
                _fact("stack.react", "React", where="technologies", expect="absent"),
                _fact("rejected.react", "React", where="rejected"),
                _fact("stack.flutter", "Flutter", where="technologies"),
            ),
        ),
        (
            "Notificaciones push cuando el cliente esté cerca de subir de nivel.",
            (_fact("req.push", "push", "notificaci"),),
        ),
        "¿Cambia mucho la estimación con Flutter respecto a la web en React?",
        (
            "Pago con Apple Pay y Google Pay para recargar el monedero.",
            (_fact("req.wallet", "Apple Pay", "Google Pay", "monedero"),),
        ),
        (
            "Modo sin conexión: el QR debe poder mostrarse aunque no haya red.",
            (_fact("req.offline", "sin conexión", "sin conexion", "offline"),),
        ),
        "¿Qué riesgos ves con las tiendas de aplicaciones?",
        (
            "Integración con el TPV de las cafeterías para sumar puntos automáticamente.",
            (_fact("req.pos", "TPV", "punto de venta"),),
        ),
        "Resume el stack actual del proyecto en una línea.",
        (
            "Analítica de uso con Firebase.",
            (_fact("tech.firebase", "Firebase"),),
        ),
        (
            "Promociones por geolocalización en un radio de 500 metros.",
            (_fact("req.geo", "geolocaliz", "radio"),),
        ),
        "¿Cuánto añade la geolocalización?",
        (
            "Accesibilidad AA en las dos apps.",
            (_fact("req.a11y", "accesib", "WCAG"),),
        ),
        (
            "Un programa de referidos: invitar a un amigo da 100 puntos.",
            (_fact("req.referral", "referid", "invitar"),),
        ),
        "¿Seguimos sin nada de React en el proyecto?",
        "¿Qué fases tendría la entrega?",
        "Dame la estimación final de Orion con todo lo acordado.",
    ),
)

VEGA = _fact("project_name", "Vega", where="project_name")
BUDGET_30 = ("30.000", "30000", "30k", "30 k", "30 000", "30 mil")
BUDGET_80 = ("80.000", "80000", "80k", "80 k", "80 000", "80 mil")

CONTRADICTION = Scenario(
    name="contradiction",
    project_type="internal_tool",
    detail_level="medium",
    turns=_turns(
        (
            "Proyecto Vega: herramienta interna para que RR. HH. gestione vacaciones y ausencias de 300 "
            "empleados, con aprobación por el responsable directo.",
            (VEGA,),
        ),
        (
            "Integración con el directorio de Google Workspace para los empleados.",
            (_fact("req.google", "Google Workspace", "Google"),),
        ),
        (
            "Importante: el presupuesto máximo es de 30.000 euros, no podemos pasar de ahí.",
            (_fact("budget", *BUDGET_30, where="constraints"),),
        ),
        (
            "Calendario compartido por equipos para ver quién está fuera.",
            (_fact("req.calendar", "calendario"),),
        ),
        "¿Qué recortarías para encajar en el presupuesto?",
        (
            "Exportación mensual a la nómina en CSV.",
            (_fact("req.payroll", "nómina", "nomina", "CSV"),),
        ),
        "¿Cuánto tardaría un equipo de 2 personas?",
        (
            "Corrección: dirección ha ampliado el presupuesto a 80.000 euros, olvidad el límite anterior.",
            (
                _fact("budget", *BUDGET_80, where="constraints"),
                _fact("budget.old", *BUDGET_30, where="constraints", expect="absent"),
            ),
        ),
        (
            "Con ese margen, añadimos una app móvil sencilla para pedir vacaciones.",
            (_fact("req.mobile", "móvil", "movil"),),
        ),
        "¿Cómo queda ahora la estimación frente al presupuesto?",
        (
            "Firma electrónica de los justificantes de baja médica.",
            (_fact("req.sign", "firma"),),
        ),
        (
            "Recordatorio automático a los responsables con solicitudes pendientes.",
            (_fact("req.reminder", "recordatorio"),),
        ),
        "¿Qué presupuesto estamos manejando?",
        (
            "Informe anual de absentismo por departamento.",
            (_fact("req.report", "absentismo"),),
        ),
        (
            "Single sign-on con las cuentas de Google.",
            (_fact("req.sso", "SSO", "single sign-on", "inicio de sesión único"),),
        ),
        "¿Algún riesgo legal con los datos de salud?",
        (
            "Cumplimiento RGPD con registro de accesos a datos sensibles.",
            (_fact("req.gdpr", "RGPD", "GDPR"),),
        ),
        "¿Qué entraría en una primera fase?",
        "Repíteme el límite de presupuesto vigente.",
        "Dame la estimación final de Vega.",
    ),
)

BURST = Scenario(
    name="burst",
    project_type="web_saas",
    detail_level="medium",
    turns=_turns(
        (
            "Proyecto Atlas: portal web de reservas para una red de 12 gimnasios, con pagos online, gestión de clases y app para entrenadores.",
            (_fact("project_name", "Atlas", where="project_name"),),
        ),
    ),
)

SCENARIOS: dict[str, Scenario] = {item.name: item for item in (GROWING, PIVOT, CONTRADICTION, BURST)}
MULTI_TURN = ("growing", "pivot", "contradiction")


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(f"escenario desconocido: {name!r} (hay {', '.join(SCENARIOS)})") from exc
