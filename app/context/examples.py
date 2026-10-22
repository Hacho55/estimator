"""Datos de referencia en bruto. El formato lo pone el preprocesamiento, no este archivo.

Cinco estimaciones vigentes y diversas, más una de 2017 que el filtro de vigencia
debe dejar fuera. Hay ruido a propósito (cliente, email, días, coma decimal, roles
sin normalizar) para que el preprocesamiento tenga algo que transformar.
"""

RATE_CARD: dict[str, dict[str, float]] = {
    "frontend": {"rate_eur_hour": 55, "min_eur_hour": 45, "max_eur_hour": 70},
    "backend": {"rate_eur_hour": 60, "min_eur_hour": 50, "max_eur_hour": 80},
    "fullstack": {"rate_eur_hour": 65, "min_eur_hour": 55, "max_eur_hour": 85},
    "ux_ui": {"rate_eur_hour": 50, "min_eur_hour": 40, "max_eur_hour": 70},
    "qa": {"rate_eur_hour": 45, "min_eur_hour": 35, "max_eur_hour": 60},
    "devops": {"rate_eur_hour": 70, "min_eur_hour": 55, "max_eur_hour": 90},
    "pm": {"rate_eur_hour": 60, "min_eur_hour": 45, "max_eur_hour": 80},
}

ESTIMATION_EXAMPLES: list[dict] = [
    {
        "id": "inv-2024",
        "title": "Plataforma web de inventario",
        "sector": "saas",
        "year": 2024,
        "client_name": "Logística Norte SL",
        "client_profile": "SaaS B2B de logística, unos 200 almacenes",
        "meeting_summary": (
            "El cliente necesita una plataforma web para gestionar stock por almacén, "
            "con roles de operario y supervisor, alertas de rotura y un panel de métricas. "
            "El diseño de pantallas todavía no existe."
        ),
        "tasks": [
            {"name": "Diseño UI", "phase": "Diseño", "role": "ux_ui", "quantity": 40, "unit": "hours"},
            {"name": "API de inventario", "phase": "Backend", "role": "backend", "quantity": 60, "unit": "hours"},
            {"name": "Roles y acceso", "phase": "Backend", "role": "backend", "quantity": 20, "unit": "hours"},
            {"name": "Panel de métricas", "phase": "Frontend", "role": "frontend", "quantity": 30, "unit": "hours"},
            {"name": "Pruebas", "phase": "Calidad", "role": "qa", "quantity": 25, "unit": "hours"},
        ],
        "team": [
            {"role": "fullstack", "count": 2, "dedication": 1},
            {"role": "ux_ui", "count": 1, "dedication": 0.5},
        ],
        "duration_weeks_min": 6,
        "duration_weeks_max": 8,
        "assumptions": [
            "Un solo idioma y una sola divisa.",
            "El cliente aporta el catálogo inicial en CSV.",
        ],
        "risks": ["Las reglas de reserva de stock pueden crecer durante el desarrollo."],
        "internal_id": "PRJ-1044",
        "created_at": "2024-03-12",
        "sales_rep_email": "comercial@empresa-interna.test",
        "payment_terms": "30 días",
        "internal_notes": "Margen comercial del 18%. No incluir en la estimación.",
        "created_by": "equipo-ventas",
    },
    {
        "id": "reservas-2025",
        "title": "App móvil de reservas",
        "sector": "movil",
        "year": 2025,
        "client_name": "Ana López",
        "client_profile": "cadena de clínicas, 12 centros",
        "meeting_summary": (
            "Ana López (ana.lopez@clinicas.test, +34 600 123 456) quiere una app iOS y Android "
            "para reservar cita, con recordatorios y un calendario para el personal. "
            "El pago en la app queda fuera de esta fase."
        ),
        "tasks": [
            {"name": "Flujos de reserva", "phase": "Diseño", "role": "diseñador UX", "quantity": "2,5", "unit": "days"},
            {"name": "App cliente", "phase": "Frontend", "role": "Front", "quantity": 50, "unit": "hours"},
            {"name": "API de citas", "phase": "Backend", "role": "backend", "quantity": "40,0", "unit": "hours"},
            {"name": "Notificaciones", "phase": "Backend", "role": "backend", "quantity": 15, "unit": "hours"},
            {"name": "Pruebas en dispositivos", "phase": "Calidad", "role": "qa", "quantity": 20, "unit": "hours"},
        ],
        "team": [
            {"role": "frontend", "count": 1, "dedication": 1},
            {"role": "backend", "count": 1, "dedication": 1},
            {"role": "ux_ui", "count": 1, "dedication": 0.4},
        ],
        "duration_weeks_min": 7,
        "duration_weeks_max": 9,
        "assumptions": [
            "Una sola marca y un solo huso horario.",
            "El centro ya tiene los huecos de agenda en su sistema actual, que exporta CSV.",
        ],
        "risks": ["Publicar en las dos tiendas puede añadir una semana de revisión."],
        "internal_id": "PRJ-1188",
        "created_at": "2025-01-20",
        "sales_rep_email": "ventas@empresa-interna.test",
        "payment_terms": "50% al inicio",
        "internal_notes": "El contacto insiste en un fijo cerrado.",
        "created_by": "equipo-ventas",
    },
    {
        "id": "erp-2024",
        "title": "Integración de pedidos con el ERP",
        "sector": "integracion",
        "year": 2024,
        "client_name": "Distribuciones Sol",
        "client_profile": "distribución, unos 5.000 pedidos al mes",
        "meeting_summary": (
            "Hay que sincronizar pedidos de la tienda online con el ERP por API, "
            "con reintentos, un panel de errores y una conciliación diaria. "
            "La tienda ya existe y no se rediseña."
        ),
        "tasks": [
            {"name": "Conector de pedidos", "phase": "Backend", "role": "backend", "quantity": 45, "unit": "hours"},
            {"name": "Reintentos y cola", "phase": "Backend", "role": "backend", "quantity": 25, "unit": "hours"},
            {"name": "Panel de incidencias", "phase": "Frontend", "role": "frontend", "quantity": 20, "unit": "hours"},
            {"name": "Conciliación", "phase": "Backend", "role": "backend", "quantity": 20, "unit": "hours"},
            {"name": "Pruebas de contrato", "phase": "Calidad", "role": "qa", "quantity": 15, "unit": "hours"},
        ],
        "team": [
            {"role": "backend", "count": 1, "dedication": 1},
            {"role": "frontend", "count": 1, "dedication": 0.5},
        ],
        "duration_weeks_min": 5,
        "duration_weeks_max": 7,
        "assumptions": ["El ERP expone una API documentada y un entorno de pruebas."],
        "risks": ["La API del ERP limita a 60 peticiones por minuto."],
        "internal_id": "PRJ-1102",
        "created_at": "2024-09-02",
        "sales_rep_email": "cuentas@empresa-interna.test",
        "payment_terms": "por hitos",
        "internal_notes": "Presupuesto hermano de la tienda, no mezclar alcances.",
        "created_by": "equipo-ventas",
    },
    {
        "id": "rrhh-2023",
        "title": "Portal interno de vacaciones",
        "sector": "interno",
        "year": 2023,
        "client_name": "Departamento de Personas",
        "client_profile": "aplicación interna, 800 empleados",
        "meeting_summary": (
            "Portal para solicitar vacaciones, con aprobación del responsable, "
            "calendario de equipo y un export mensual a nóminas. "
            "Solo escritorio y usuarios internos."
        ),
        "tasks": [
            {"name": "Flujos de aprobación", "phase": "Diseño", "role": "ux_ui", "quantity": 15, "unit": "hours"},
            {"name": "Solicitudes y saldo", "phase": "Backend", "role": "backend", "quantity": 35, "unit": "hours"},
            {"name": "Calendario", "phase": "Frontend", "role": "frontend", "quantity": 25, "unit": "hours"},
            {"name": "Export a nóminas", "phase": "Backend", "role": "backend", "quantity": 15, "unit": "hours"},
            {"name": "Pruebas", "phase": "Calidad", "role": "qa", "quantity": 10, "unit": "hours"},
        ],
        "team": [
            {"role": "fullstack", "count": 1, "dedication": 1},
            {"role": "qa", "count": 1, "dedication": 0.3},
        ],
        "duration_weeks_min": 4,
        "duration_weeks_max": 6,
        "assumptions": ["El directorio de empleados llega por CSV semanal."],
        "risks": ["Las reglas de convenio pueden pedir más tipos de ausencia."],
        "internal_id": "PRJ-0980",
        "created_at": "2023-05-18",
        "sales_rep_email": "interno@empresa-interna.test",
        "payment_terms": "interno, sin factura",
        "internal_notes": "Proyecto de coste, no de margen.",
        "created_by": "oficina-proyectos",
    },
    {
        "id": "ecom-2025",
        "title": "Tienda online con catálogo propio",
        "sector": "ecommerce",
        "year": 2025,
        "client_name": "Taller Alba",
        "client_profile": "e-commerce de mobiliario, unos 50.000 usuarios al mes",
        "meeting_summary": (
            "Catálogo, ficha de producto, carrito y checkout con un proveedor de pago ya contratado. "
            "Hace falta un buscador con filtros y un área de cliente con pedidos. "
            "El diseño visual llega en Figma."
        ),
        "tasks": [
            {"name": "Maquetación del catálogo", "phase": "Frontend", "role": "frontend", "quantity": 40, "unit": "hours"},
            {"name": "Carrito y checkout", "phase": "Frontend", "role": "frontend", "quantity": 35, "unit": "hours"},
            {"name": "API de catálogo y pedidos", "phase": "Backend", "role": "backend", "quantity": 55, "unit": "hours"},
            {"name": "Pago", "phase": "Backend", "role": "backend", "quantity": 20, "unit": "hours"},
            {"name": "Búsqueda", "phase": "Backend", "role": "backend", "quantity": 20, "unit": "hours"},
            {"name": "Pruebas de compra", "phase": "Calidad", "role": "qa", "quantity": 20, "unit": "hours"},
        ],
        "team": [
            {"role": "frontend", "count": 1, "dedication": 1},
            {"role": "backend", "count": 1, "dedication": 1},
            {"role": "qa", "count": 1, "dedication": 0.5},
        ],
        "duration_weeks_min": 8,
        "duration_weeks_max": 10,
        "assumptions": [
            "El proveedor de pago ofrece un entorno sandbox.",
            "Un solo país de envío en esta fase.",
        ],
        "risks": ["Las promociones avanzadas quedan fuera y suelen pedir hueco después."],
        "internal_id": "PRJ-1210",
        "created_at": "2025-06-03",
        "sales_rep_email": "retail@empresa-interna.test",
        "payment_terms": "tres hitos",
        "internal_notes": "Revisar si el hosting lo pone el cliente.",
        "created_by": "equipo-ventas",
    },
    {
        "id": "web-2017",
        "title": "Web corporativa estática",
        "sector": "web",
        "year": 2017,
        "client_name": "Cliente Antiguo",
        "client_profile": "web corporativa de cinco páginas",
        "meeting_summary": (
            "Web institucional con formulario de contacto. Tarifas de aquella época, "
            "anteriores a la tabla actual. No debe usarse como referencia."
        ),
        "tasks": [
            {"name": "Páginas estáticas", "phase": "Frontend", "role": "frontend", "quantity": 30, "unit": "hours"},
            {"name": "Formulario", "phase": "Backend", "role": "backend", "quantity": 10, "unit": "hours"},
        ],
        "team": [{"role": "fullstack", "count": 1, "dedication": 1}],
        "duration_weeks_min": 2,
        "duration_weeks_max": 3,
        "assumptions": ["Contenido entregado en Word."],
        "risks": ["El hosting compartido de entonces limitaba el correo."],
        "internal_id": "PRJ-0200",
        "created_at": "2017-04-01",
        "sales_rep_email": "archivo@empresa-interna.test",
        "payment_terms": "contado",
        "internal_notes": "Tarifas de 2017, no reutilizar.",
        "created_by": "archivo",
    },
]
