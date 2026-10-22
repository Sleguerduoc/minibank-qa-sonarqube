<#
  MiniBank 3 - ciclo SonarQube (ISY1102)
  Uso (PowerShell, desde la carpeta Entregables\SonarQube):
      powershell -ExecutionPolicy Bypass -File .\sonar_ciclo.ps1 -Version 3.0.0-sast-lab
      powershell -ExecutionPolicy Bypass -File .\sonar_ciclo.ps1 -Version 3.1.0
      powershell -ExecutionPolicy Bypass -File .\sonar_ciclo.ps1 -Version 3.2.0

  Qué hace en cada ciclo:
    1. Levanta SonarQube Community en http://localhost:9000 (Docker) si no está corriendo.
    2. Si la versión no es 3.0, copia los archivos corregidos de builds\<version> sobre minibank-qa-lab-v3.
    3. Reconstruye MiniBank con docker compose, verifica /api/lab/status y ejecuta pytest (pruebas de comportamiento).
    4. Genera un token de análisis temporal (solo en memoria), ejecuta el scanner y espera el procesamiento.
    5. Exporta los resultados del servidor (issues, hotspots, métricas, quality gate) a resultados\<version>\.
    6. Revoca el token.
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$SoloExportar   # no aplica build ni analiza: solo exporta lo que ya procesó el servidor
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Aqui      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Raiz      = Resolve-Path (Join-Path $Aqui "..\..")
$V3        = Join-Path $Raiz "proyecto\calidad_qa\minibank-qa-lab-v3"
$Builds    = Join-Path $Aqui "builds"
$Salida    = Join-Path $Aqui ("resultados\" + $Version)
$Sonar     = "http://localhost:9000"
$Proyecto  = "minibank3"
New-Item -ItemType Directory -Force -Path $Salida | Out-Null
$Log = Join-Path $Salida "registro_ciclo.txt"
function Ejecutar([scriptblock]$Bloque, [string]$Archivo) {
    # Ejecuta un comando nativo sin que PowerShell trate stderr como excepción.
    $anterior = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $salidaCmd = & $Bloque 2>&1 | ForEach-Object { "$_" }
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $anterior
    if ($Archivo) { $salidaCmd | Out-File $Archivo -Encoding utf8 }
    return $codigo
}
function Paso($t) { $l = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $t; Write-Host $l -ForegroundColor Cyan; Add-Content -Path $Log -Value $l -Encoding UTF8 }

if (-not (Test-Path $V3)) { throw "No encuentro la carpeta $V3" }
Paso "Ciclo $Version | carpeta V3: $V3"
$dv = Ejecutar { docker version --format "{{.Server.Version}}" } (Join-Path $Salida "docker_version.txt")
if ($dv -ne 0) { throw "Docker no responde. Abra Docker Desktop y espere a que diga 'Engine running'." }
Paso ("Docker Engine " + (Get-Content (Join-Path $Salida "docker_version.txt") -Raw).Trim())

# ---------- 1. SonarQube ----------
if ((Ejecutar { docker network inspect sonarnet } $null) -ne 0) { Ejecutar { docker network create sonarnet } $null | Out-Null }
$existe = docker ps -a --filter "name=^sonarqube$" --format "{{.Names}}"
if (-not $existe) {
    Paso "Creando contenedor SonarQube (primera vez: descarga ~1 GB, puede tardar)"
    $c = Ejecutar { docker run -d --name sonarqube --network sonarnet -p 127.0.0.1:9000:9000 `
        -e SONAR_ES_BOOTSTRAP_CHECKS_DISABLE=true `
        -v sonarqube_data:/opt/sonarqube/data -v sonarqube_extensions:/opt/sonarqube/extensions `
        sonarqube:community } (Join-Path $Salida "sonarqube_run.txt")
    if ($c -ne 0) { throw "No se pudo crear el contenedor SonarQube. Ver $Salida\sonarqube_run.txt" }
} else { Ejecutar { docker start sonarqube } $null | Out-Null }

Paso "Esperando a que SonarQube esté UP"
for ($i = 0; $i -lt 120; $i++) {
    try { $st = (Invoke-RestMethod "$Sonar/api/system/status").status } catch { $st = "..." }
    if ($st -eq "UP") { break }
    Start-Sleep -Seconds 5
}
if ($st -ne "UP") { throw "SonarQube no quedó disponible (estado: $st). Revise 'docker logs sonarqube'." }
$sv = Invoke-RestMethod "$Sonar/api/server/version"
Paso "SonarQube UP - versión $sv"

# ---------- credenciales de administrador (solo en memoria) ----------
function Auth($user, $pass) { @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${user}:${pass}")) } }
$porDefecto = $false
try { Invoke-RestMethod "$Sonar/api/authentication/validate" -Headers (Auth "admin" "admin") | ForEach-Object { $porDefecto = $_.valid } } catch {}
if ($porDefecto) {
    Write-Host "Primera vez: defina una NUEVA clave para el usuario admin de SonarQube (min. 12 caracteres, mayuscula, minuscula, numero y simbolo). Anotela." -ForegroundColor Yellow
    $AdminPass = $null
    for ($intento = 1; $intento -le 5 -and -not $AdminPass; $intento++) {
        $s1 = Read-Host "Nueva clave admin" -AsSecureString
        $s2 = Read-Host "Repita la nueva clave" -AsSecureString
        $nueva = [System.Net.NetworkCredential]::new('', $s1).Password
        if ($nueva -ne [System.Net.NetworkCredential]::new('', $s2).Password) { Write-Host "Las claves no coinciden." -ForegroundColor Red; continue }
        try {
            Invoke-RestMethod -Method Post "$Sonar/api/users/change_password" -Headers (Auth "admin" "admin") `
                -Body @{ login = "admin"; previousPassword = "admin"; password = $nueva } | Out-Null
            $AdminPass = $nueva
        } catch {
            $detalle = $_.ErrorDetails.Message
            if (-not $detalle) { $detalle = $_.Exception.Message }
            Write-Host "SonarQube rechazo la clave: $detalle" -ForegroundColor Red
        }
    }
    if (-not $AdminPass) { throw "No se pudo definir la clave de admin." }
} else {
    $s1 = Read-Host "Clave del usuario admin de SonarQube" -AsSecureString
    $AdminPass = [System.Net.NetworkCredential]::new('', $s1).Password
}
$H = Auth "admin" $AdminPass
if (-not (Invoke-RestMethod "$Sonar/api/authentication/validate" -Headers $H).valid) { throw "Clave admin incorrecta" }

# proyecto
$proj = Invoke-RestMethod "$Sonar/api/projects/search?projects=$Proyecto" -Headers $H
if ($proj.components.Count -eq 0) {
    Invoke-RestMethod -Method Post "$Sonar/api/projects/create" -Headers $H -Body @{ project = $Proyecto; name = "MiniBank 3 - Laboratorio de seguridad" } | Out-Null
    Paso "Proyecto $Proyecto creado"
}

if (-not $SoloExportar) {
# ---------- 2. aplicar build corregida ----------
$dirBuild = Join-Path $Builds $Version
if (Test-Path $dirBuild) {
    Paso "Aplicando archivos de builds\$Version sobre minibank-qa-lab-v3"
    Get-ChildItem $dirBuild -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($dirBuild.Length + 1)
        $dest = Join-Path $V3 $rel
        New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
        Copy-Item $_.FullName $dest -Force
        Add-Content -Path $Log -Value ("   copiado: " + $rel) -Encoding UTF8
    }
} else { Paso "Sin archivos para aplicar: se analiza el código tal como está (punto de partida)" }

# ---------- 3. reconstruir MiniBank y pruebas de comportamiento ----------
Push-Location $V3
try {
    Paso "docker compose up --build -d"
    $c = Ejecutar { docker compose up --build -d } (Join-Path $Salida "docker_compose_up.txt")
    if ($c -ne 0) { throw "docker compose falló. Ver $Salida\docker_compose_up.txt" }
    for ($i = 0; $i -lt 40; $i++) {
        try { $lab = Invoke-RestMethod "http://localhost:8002/api/lab/status"; if ($lab.status -eq "ready") { break } } catch {}
        Start-Sleep -Seconds 3
    }
    $lab | ConvertTo-Json | Out-File (Join-Path $Salida "lab_status.json") -Encoding utf8
    Paso ("MiniBank /api/lab/status: status={0} build={1}" -f $lab.status, $lab.build)
    Ejecutar { docker compose ps } (Join-Path $Salida "docker_compose_ps.txt") | Out-Null
    Paso "pytest dentro del contenedor backend"
    Ejecutar { docker compose exec -T backend python -m pytest -v -p no:cacheprovider } (Join-Path $Salida "pytest.txt") | Out-Null
    Get-Content (Join-Path $Salida "pytest.txt") | Select-Object -Last 1 | ForEach-Object { Paso "pytest: $_" }
} finally { Pop-Location }

# ---------- 4. análisis ----------
$tokName = "analisis-" + $Version + "-" + (Get-Date -Format "yyyyMMddHHmmss")
$tok = Invoke-RestMethod -Method Post "$Sonar/api/user_tokens/generate" -Headers $H -Body @{ name = $tokName; type = "PROJECT_ANALYSIS_TOKEN"; projectKey = $Proyecto }
$env:SONAR_TOKEN = $tok.token
try {
    Paso "Ejecutando SonarScanner (sonar-project.properties, versión $Version)"
    $v3Docker = $V3 -replace '\\', '/'
    $c = Ejecutar { docker run --rm --network sonarnet -e SONAR_HOST_URL="http://sonarqube:9000" -e SONAR_TOKEN `
        -v "${v3Docker}:/usr/src" sonarsource/sonar-scanner-cli `
        "-Dsonar.projectKey=$Proyecto" "-Dsonar.projectVersion=$Version" } (Join-Path $Salida "scanner.txt")
    if ($c -ne 0) { throw "El scanner terminó con error. Ver $Salida\scanner.txt" }
    # El id de la tarea se toma de la salida del scanner (la carpeta .scannerwork queda dentro del contenedor).
    $m = Select-String -Path (Join-Path $Salida "scanner.txt") -Pattern "api/ce/task\?id=([0-9a-fA-F-]+)" | Select-Object -Last 1
    if ($m) { $ceId = $m.Matches[0].Groups[1].Value }
    else {
        $rt = Get-Content (Join-Path $V3 ".scannerwork\report-task.txt")
        $ceId = ($rt | Where-Object { $_ -like "ceTaskId=*" }) -replace "ceTaskId=", ""
    }
    Paso "Esperando procesamiento del servidor (tarea $ceId)"
    for ($i = 0; $i -lt 120; $i++) {
        $task = (Invoke-RestMethod "$Sonar/api/ce/task?id=$ceId" -Headers $H).task
        if ($task.status -in @("SUCCESS", "FAILED", "CANCELED")) { break }
        Start-Sleep -Seconds 3
    }
    $task | ConvertTo-Json -Depth 5 | Out-File (Join-Path $Salida "ce_task.json") -Encoding utf8
    Paso "Tarea del servidor: $($task.status)"
} finally {
    Remove-Item Env:SONAR_TOKEN -ErrorAction SilentlyContinue
    Invoke-RestMethod -Method Post "$Sonar/api/user_tokens/revoke" -Headers $H -Body @{ name = $tokName } | Out-Null
    Paso "Token de análisis revocado"
}
} else { Paso "Modo -SoloExportar: se exporta el último análisis procesado por el servidor" }

# ---------- 5. exportar resultados ----------
function Guardar($url, $archivo) {
    try { (Invoke-WebRequest "$Sonar$url" -Headers $H -UseBasicParsing).Content | Out-File (Join-Path $Salida $archivo) -Encoding utf8 }
    catch { "ERROR: $($_.Exception.Message)" | Out-File (Join-Path $Salida $archivo) -Encoding utf8 }
}
"$sv" | Out-File (Join-Path $Salida "sonarqube_version.txt") -Encoding utf8
Guardar "/api/issues/search?components=$Proyecto&ps=500&additionalFields=_all" "issues_todas.json"
Guardar "/api/issues/search?components=$Proyecto&ps=500&impactSoftwareQualities=SECURITY&issueStatuses=OPEN,CONFIRMED" "issues_seguridad_abiertas.json"
Guardar "/api/issues/search?components=$Proyecto&ps=500&types=VULNERABILITY&resolved=false" "issues_vulnerabilidades.json"
Guardar "/api/hotspots/search?project=$Proyecto&ps=500" "hotspots.json"
Guardar "/api/measures/component?component=$Proyecto&metricKeys=security_issues,vulnerabilities,security_hotspots,security_rating,security_review_rating,security_hotspots_reviewed,reliability_issues,bugs,maintainability_issues,code_smells,ncloc,coverage,duplicated_lines_density,alert_status,software_quality_security_issues,software_quality_security_rating" "medidas.json"
Guardar "/api/qualitygates/project_status?projectKey=$Proyecto" "quality_gate.json"
Guardar "/api/qualityprofiles/search?project=$Proyecto" "quality_profiles.json"
Guardar "/api/project_analyses/search?project=$Proyecto" "historial_analisis.json"
Guardar "/api/plugins/installed" "analizadores.json"
Guardar "/api/ce/activity?component=$Proyecto&ps=20" "ce_actividad.json"

$medidas = (Get-Content (Join-Path $Salida "medidas.json") -Raw | ConvertFrom-Json).component.measures
Paso "RESUMEN $Version"
$medidas | Sort-Object metric | ForEach-Object { $l = "   {0,-36} {1}" -f $_.metric, $_.value; Write-Host $l; Add-Content -Path $Log -Value $l -Encoding UTF8 }
Write-Host ""
Write-Host "Listo. Resultados en: $Salida" -ForegroundColor Green
Write-Host "Abra $Sonar/dashboard?id=$Proyecto (usuario admin) y tome las capturas indicadas en el README." -ForegroundColor Green
