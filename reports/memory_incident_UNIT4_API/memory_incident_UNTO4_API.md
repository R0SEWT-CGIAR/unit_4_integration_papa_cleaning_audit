
# Memory Incident: UNIT4_API

## 1. Resumen ejecutivo

La ejecución de un script previamente validado en el entorno de prueba (CIPTEST) fue realizada en el entorno productivo (PROD), donde una solicitud válida según el contrato expuesto permitió una descarga masiva no acotada de documentos. Esta operación derivó en un agotamiento de memoria y posterior indisponibilidad del servicio.

El incidente ocurrió aproximadamente a las **14:30 del 4 de febrero de 2026** y estuvo asociado a la inclusión de contenido de archivos (`fileContent`) en formato Base64 dentro de una enumeración completa de documentos.

---

## 2. Descripción del incidente

Durante la ejecución en PROD, se solicitó la recuperación de todos los documentos disponibles en una única respuesta incluyendo el campo `fileContent` (Base64).  
Dado que el endpoint permite enumeración completa sin paginación obligatoria, la respuesta materializó un volumen elevado de datos en memoria, provocando agotamiento de recursos.

La ejecución se interrumpió debido a la indisponibilidad del servicio, causada por el consumo de memoria no acotado.

---

## 3. Análisis de causa raíz

La causa raíz del incidente fue la **ausencia de controles obligatorios de paginación y límites de volumen** en el consumo del endpoint de documentos, lo que permitió una enumeración completa no acotada.

Este comportamiento fue **amplificado** por la inclusión de `fileContent` en formato Base64, que incrementa significativamente el tamaño de los objetos en memoria y agrava el impacto de una respuesta masiva.

El incidente no fue producto de un bypass o uso indebido, sino de un patrón de consumo permitido por el contrato actual de la API.

---

## 4. Evaluación de riesgos

| ID | Riesgo                     | Descripción técnica                                                           | Impacto                         | Probabilidad | Nivel          |
| -- | -------------------------- | ----------------------------------------------------------------------------- | ------------------------------- | ------------ | -------------- |
| R1 | Enumeración sin límite     | El endpoint permite solicitar todos los documentos sin paginación obligatoria | Alto (OOM / caída del servicio) | Media        | **Alto**       |
| R2 | Payload inflado por Base64 | Inclusión de `fileContent` multiplica el uso de memoria por documento         | Alto                            | Media        | **Alto**       |
| R3 | Acumulación en memoria     | Respuesta única materializa todos los objetos antes de persistencia           | Alto                            | Media        | **Alto**       |
| R4 | Ausencia de rate limiting  | Requests consecutivas sin control temporal                                    | Medio–Alto                      | Media        | **Medio-Alto** |
| R5 | Falta de circuit breaker   | Fallos consecutivos no detienen la ejecución automáticamente                  | Medio                           | Baja–Media   | **Medio**      |
| R6 | Falta de checkpoint        | Reintentos obligan a reprocesar lotes completos                               | Medio                           | Media        | **Medio**      |
| R7 | Baja observabilidad previa | No existían métricas por tipo/volumen de documento                            | Medio                           | Media        | **Medio**      |

---

## 5. Medidas de mitigación implementadas

Se añadieron controles de carga, persistencia segura y registro de métricas en
[`unit4_audit.py`](../../unit4_audit.py), con el objetivo de eliminar la posibilidad de agotamiento de memoria y permitir una operación controlada.

### Controles implementados

* Rate limit configurable (`UNIT4_MIN_INTERVAL`)
* Backoff + retries para respuestas 429 / 5xx / timeouts
* Circuit breaker tras fallas consecutivas
* Checkpoint + resume para paginación
* Persistencia incremental en formato JSONL (stream)
* Métricas de volumen por `docType`

### Tabla de medidas de mitigación

| ID | Medida                                | Tipo       | Riesgo mitigado | Estado       |
| -- | ------------------------------------- | ---------- | --------------- | ------------ |
| M1 | Rate limit configurable               | Preventiva | R1, R4          | Implementada |
| M2 | Backoff + retries                     | Correctiva | R4              | Implementada |
| M3 | Circuit breaker                       | Preventiva | R1, R5          | Implementada |
| M4 | Paginación controlada (`UNIT4_LIMIT`) | Preventiva | R1              | Implementada |
| M5 | Checkpoint + resume                   | Correctiva | R6              | Implementada |
| M6 | Persistencia incremental (JSONL)      | Preventiva | R2, R3          | Implementada |
| M7 | Métricas por `docType`                | Detectiva  | R7              | Implementada |
| M8 | Separación metadata / contenido       | Preventiva | R1, R2          | Implementada |
| M9 | Configuración operativa por entorno   | Preventiva | R1–R6           | Implementada |

---

## 6. Archivos de soporte generados

* `repinv_docs_metrics.json`
* `reptec_docs_metrics.json`
* `repinv_docs_checkpoint.json`
* `reptec_docs_checkpoint.json`
* `repinv_docs_items.jsonl`
* `reptec_docs_items.jsonl`

---

## 7. Variables de entorno recomendadas

```bash
UNIT4_LIMIT=50
UNIT4_MIN_INTERVAL=0.5
UNIT4_MAX_RETRIES=3
UNIT4_OUT_DIR=artifacts
```

---

## 8. Recomendaciones operativas

1. **Nunca** solicitar `withFileContent=true` sin paginación explícita.
2. Descargar primero metadata (sin `fileContent`) y luego el contenido por `id`, aplicando throttling.
3. Ante incidentes o alta carga, ajustar `UNIT4_MIN_INTERVAL` a valores entre **0.5–1.0s**.

---

## 9. Conclusión

El incidente permitió identificar un patrón de consumo riesgoso habilitado por el contrato actual de la API.
Las medidas implementadas introducen controles preventivos, correctivos y detectivos que eliminan la posibilidad de agotamiento de memoria por descargas masivas y establecen un marco de operación seguro y reanudable bajo carga.

