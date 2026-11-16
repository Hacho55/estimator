# 0003 · Perfiles con estructura propia; el tier llega firmado

**Estado:** aceptada (sesión 5, fase 3).

## Contexto

Un ingeniero y una directora comercial no necesitan la misma respuesta. Cambiar solo el tono
es cosmético; dejar que el cliente elija el perfil en el cuerpo permite pedir el modo más caro.

## Decisión

- **Tres capas.** El tier vive en el backend de negocio (`users.tier`), viaja en un JWT HS256
  firmado (`sub`, `tier`, `aud`, `exp`) y el estimador lo valida (`app/security/caller.py`). La
  sesión pertenece al `sub` que la creó.
- **Estructura, no tono.** Cada tier tiene plantilla (`v4/system_<tier>.j2`, montada con
  parciales), schema de salida y vista propios (`app/tiers.py`).
- **Dos schemas por tier.** El modelo devuelve un desglose común (rol y horas) y lo propio del
  perfil; el código calcula tarifas, costes, totales y rangos (`tier_views.py`). La aritmética
  sigue fuera del modelo.
- **El tier fija la estructura en `/sessions`** (D7): sale `output_format`; `detail_level` queda
  como profundidad. `/api/v1` y `/api/v2` no cambian. `/sessions` no estaba publicado (D15).
- **Pasos por tier** (D9): herramientas y revisión se declaran en `TIER_CONFIG`.
- **Backend de negocio aparte** (D5): FastAPI con su SQLite; el estimador le habla por HTTP.

## Alternativas descartadas

- **Tier deducido del texto o como parámetro (la referencia):** antipatrón «tier en el cliente».
- **Una plantilla con un bloque de audiencia y un schema:** antipatrón «solo cambia el tono».
- **Cabecera simple:** el front es un navegador, no hay red privada.

## Consecuencias

Una caché del prefijo por tier; `prompt_version` incluye el tier. Login de demostración, sin
contraseñas: la autenticación real queda fuera de alcance.

## Revisar cuando

Aparezca la necesidad de un tier compuesto: antes de añadirlo, revisar si un tier está mal definido.
