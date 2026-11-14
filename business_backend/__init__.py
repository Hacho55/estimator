"""Backend de negocio simulado (sesión 5, D5).

Es otro servicio, con su propia BBDD (SQLite) y sus reglas. El estimador no importa
nada de aquí ni abre su base de datos: le habla por HTTP con contrato
(``POST /api/internal/similar_projects``) y recibe de él el perfil del usuario en un
JWT firmado. Es la separación de capas de la fase 1 (§4) y la capa 1 de los tiers
(fase 3).
"""
