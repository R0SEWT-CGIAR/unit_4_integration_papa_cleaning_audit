# Memory Incident: UNTO4_API

## Resumen
La ejecución del script fue validada previamente en el entorno de prueba (CIPTEST). Al ejecutarse en el entorno (PROD), la solicitud valida segun el contrato expuesto permitió una descarga masiva sin límite, lo que derivó en agotamiento de memoria.

El incidente a promediar las 14:30 del 4 de Febrero de 2026, ocasionado al solicitar todos los documentos en una única respuesta incluyendo `fileContent` (Base64), lo cual incrementa significativamente el tamaño de los objetos en memoria y amplifica el impacto de una enumeración completa.

## Medidas de mitigación implementadas
Se añadieron controles de carga y registro de métricas en [unit4_audit.py](../unit4_audit.py):

- Rate limit configurable (`UNIT4_MIN_INTERVAL`).
- Backoff + retries para 429/5xx/timeouts.
- Circuit breaker tras fallas consecutivas.
- Checkpoint + resume.
- JSONL incremental (stream).
- Métricas por docType.

## Archivos de medidas (métricas/checkpoints)
- repinv_docs_metrics.json, reptec_docs_metrics.json
- repinv_docs_checkpoint.json, reptec_docs_checkpoint.json
- repinv_docs_items.jsonl, reptec_docs_items.jsonl

## Variables de entorno recomendadas
- UNIT4_LIMIT=50
- UNIT4_MIN_INTERVAL=0.5
- UNIT4_MAX_RETRIES=3
- UNIT4_OUT_DIR=artifacts

## Recomendación operativa implementada
1) Nunca pedir `withFileContent=true` sin paginación.
2) Descargar primero metadata (sin `fileContent`) y luego el contenido por `id` con throttling.
3) Si hay incidentes, ajustar `UNIT4_MIN_INTERVAL` a 0.5–1.0s.
