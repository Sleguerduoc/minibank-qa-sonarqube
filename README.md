# MiniBank 3 · Análisis y corrección con SonarQube

ISY1102 Seguridad y calidad en el desarrollo de software (DuocUC).
Sebastián Seguel y Pablo Díaz.

Partimos del laboratorio MiniBank QA Lab 3.0 del curso ([calidad_qa](https://github.com/anyeloCastellon/calidad_qa)), lo analizamos con SonarQube y corregimos el código por ciclos hasta que no quedaron problemas de seguridad:

Analizar → Detectar → Documentar → Corregir → Nueva versión → Volver a analizar

Cada versión es un commit con su etiqueta, así que los cambios entre una y otra se pueden ver directo en GitHub:

| Versión | Problemas de seguridad (Overall Code) | Alta / Media / Baja | Calificación | pytest |
|---|---|---|---|---|
| [v3.0.0-sast-lab](https://github.com/Sleguerduoc/minibank-qa-sonarqube/tree/v3.0.0-sast-lab) | 24 | 13 / 6 / 5 | D | 8 / 8 |
| [v3.1.0](https://github.com/Sleguerduoc/minibank-qa-sonarqube/tree/v3.1.0) | 4 | 2 / 0 / 2 | D | 19 / 19 |
| [v3.2.0](https://github.com/Sleguerduoc/minibank-qa-sonarqube/tree/v3.2.0) | **0** | 0 / 0 / 0 | A | 23 / 23 |

Comparaciones: [3.0 → 3.1](https://github.com/Sleguerduoc/minibank-qa-sonarqube/compare/v3.0.0-sast-lab...v3.1.0) · [3.1 → 3.2](https://github.com/Sleguerduoc/minibank-qa-sonarqube/compare/v3.1.0...v3.2.0)

## Qué cambió en cada versión

**3.1.0** corrige los 20 hallazgos de `backend/app/security_lab.py` y `frontend/src/Documentos.jsx`: claves escritas en el código, DES y AES-CBC con IV fijo, MD5/SHA-1, RSA de 1024 bits, `mktemp` con `chmod 777`, `random` para códigos, cookie sin Secure/HttpOnly, TLS 1.1 sin verificar certificado, URL http y `postMessage('*')`. También deja de escribir la clave en el log cuando el login falla.

**3.2.0** corrige los 4 de los Dockerfile (contenedores como root y `COPY . .`). Además arregla los defectos que encontramos en la ejecución de pruebas (Actividad 2.3.2): token guardado en localStorage, rechazo por saldo que sumaba al acumulado diario, movimientos que no se actualizaban y alias guardado con espacios. Las claves ahora se guardan con hash scrypt.

Los 24 hallazgos quedaron en estado FIXED. No usamos `NOSONAR`, no desactivamos reglas ni marcamos falsos positivos.

El Quality Gate aparece en Failed en 3.1 y 3.2 por la cobertura del código nuevo (0 %, pide 80 %): el análisis no recibe un reporte de cobertura. No tiene que ver con seguridad. Quedan abiertos 4 problemas de confiabilidad (`javascript:S9011`, botones sin `type`).

## Evidencias

`sonarqube/resultados/<versión>/` tiene lo que exportamos del servidor en cada ciclo (issues, medidas, Quality Gate, perfiles), la salida de pytest y del scanner, y capturas de SonarQube. En 3.2.0 están además la regresión de los casos de prueba en Chrome (`regresion_3.2.0.txt`, `EV-32-*.png`) y el fallo del primer intento por la base creada como root (`intento1_*.png`).

`sonarqube/sonar_ciclo.ps1` es el script con que corrimos cada ciclo (SonarQube Community 26.10 y sonar-scanner en Docker). Está pensado para la estructura de carpetas del curso, donde aplicaba cada versión sobre `minibank-qa-lab-v3`.

## Cómo levantarlo

```powershell
docker compose up --build -d
docker compose exec backend python -m pytest -q
```

App en http://localhost:5175 · API en http://localhost:8002/docs · RUT 11.111.111-1, clave 1234 (datos ficticios del laboratorio).

Si vienes de una versión anterior con la base ya creada, mueve `backend/data/minibank_v3.db*` antes de levantar la 3.2: la crearon contenedores como root y el usuario nuevo no puede escribir en ella.

El README original del laboratorio está en [README_LAB.md](README_LAB.md).
