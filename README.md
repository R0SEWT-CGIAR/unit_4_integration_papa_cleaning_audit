# Audits

Carpeta para scripts de auditoría y exploración de datos antes de integración.

## Unit4 Document Sample

Descarga de ~100 documentos de cada tipo para análisis en ask-papa-ai-dx.

### Archivos Generados

Por defecto se guardan bajo `artifacts/` (configurable con `UNIT4_OUT_DIR`).

**CSVs - Metadata Esencial (9 campos):**
- `repinv_docs_metadata.csv`, `reptec_docs_metadata.csv`
- Columns: `id`, `fileName`, `mimeType`, `docType`, `companyId`, `status`, `revisionNo`, `updatedAt`, `updatedBy`
- Uso: Análisis rápido, importación simplificada

**CSVs - Metadata COMPLETA (14 campos, SIN EXCEPCIÓN):**
- `repinv_complete_metadata.csv`, `reptec_complete_metadata.csv`
- **Todos los campos de la API:** companyId, docType, mimeType, id, status, revisionNo, fileName, checkoutUserId, lastUpdate_updatedAt, lastUpdate_updatedBy, indexes, title, description, expiryDate
- Tamaño: ~15 KB
- Uso: Análisis exhaustivo, documentación de estructura completa

**JSONs (respuesta API):**
- Estructura: `{start, limit, count, total, items[]}`
- Cada item contiene: todos los 14 campos
- **Sin `fileContent`**: Se guarda en archivos binarios, no en JSON
- Tamaño: ~36 KB
- Uso: Documentación de estructura API, testing de parsers JSON

### Análisis Exploratorio

El notebook `unit4_exploration.ipynb` incluye:
- Estadísticas básicas (count, tipos únicos, distribuciones)
- Análisis de tipos de archivo (mimeType, extensiones)
- Patrones temporales (updatedAt timestamps)
- Usuarios más activos (updatedBy)
- Visualizaciones de tendencias
- Recomendaciones para integración Unit4Sync

### Estructura de salida

```
artifacts/
  docs/            # binarios descargados por docType
  csv/             # metadata csv
  json/            # respuestas JSON sin fileContent
  items/           # items JSONL por docType (stream)
  checkpoints/     # checkpoints de paginación
  metrics/         # métricas por docType
  logs/            # logs de ejecución (stdout/stderr)
```

### Cómo usar

1. **Documentos binarios**: Decodificados de Base64, listos para procesar
   - PDF, DOCX, XLSX, etc.

2. **CSVs**: Metadata sin fileContent (tamaño pequeño, fácil de explorar)
   ```bash
   pandas.read_csv('repinv_docs_metadata.csv')
   ```

3. **JSONs**: Estructura completa de respuesta API (para validación/testing)
   ```bash
   json.load(open('repinv_docs_response.json'))
   ```

4. Para integración Unit4Sync en ask-papa-ai-dx:
   - Usar JSONs para entender estructura exacta de respuestas
   - Usar CSVs para análisis exploratorio
   - Copiar carpetas a `backend/app/tests/fixtures/unit4_samples/`
   - O servir via API de prueba para testing dry-run

### Generación

```bash
cd /home/rody/Code/audits
python unit4_audit.py
# Crea: artifacts/docs, artifacts/csv, artifacts/json, artifacts/items, artifacts/checkpoints, artifacts/metrics

# Análisis exploratorio
jupyter notebook unit4_exploration.ipynb

```

## Incidente (memoria / UNIT4_API)

Resumen y medidas en [audits/memory_incident_UNTO4_API.md](audits/memory_incident_UNTO4_API.md).

### API Details (Unit4 BusinessWorld)

**Base URL:** `https://XXXX/BusinessWorld-web-api/v1`

**Documentos encontrados:**
- **REPTEC**: 581 documentos
- **REPINV**: 6572 documentos

**Response structure:**
```json
{
  "start": 0,
  "limit": 1,
  "count": 1,
  "total": 581,
  "items": [
    {
      "id": "74bcce65-99e7-4564-89af-a08e921ac9ef",
      "fileName": "0.Rapport Avancement 6.pdf",
      "mimeType": "application/pdf",
      "docType": "REPTEC",
      "companyId": "P2",
      "status": "N",
      "revisionNo": 1,
      "lastUpdate": {
        "updatedAt": "2025-04-30T20:24:58.000",
        "updatedBy": "MARCOPINTO"
      }
      // fileContent NOT included by default
    }
  ]
}
```

**Important notes:**
- `fileContent` is NOT included in list responses
- Must fetch individual document: `GET /documents/{id}?withFileContent=true` to get Base64 content
- Authentication: Requires session/login (not simple Basic Auth)
- `total` field contains total document count
- `indexes` parameter needed for authorization