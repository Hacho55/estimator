---
name: update-changelog
description: Actualiza CHANGELOG.md del estimador con el formato Keep a Changelog en español. Úsala al cerrar un cambio, al preparar una versión o cuando el usuario pida anotar qué cambió.
---

# Actualizar el changelog

`CHANGELOG.md` sigue Keep a Changelog y SemVer. Categorías: Añadido, Cambiado, Corregido, Eliminado.

- El cambio del día va bajo `## [Unreleased]`, en la categoría que corresponda.
- Un cambio de prompt incluye el `prompt_version` (`v1-` más el hash).
- Al publicar, mueve las entradas de `[Unreleased]` a `## [x.y.z] - AAAA-MM-DD` y deja `[Unreleased]` vacío.
- Parche si corriges sin cambiar el contrato, menor si añades algo compatible, mayor si rompes `/api/v1`.
