# Arranca WSL2 (Ubuntu) y Postgres para desarrollo local.
# Correrlo antes de `alembic upgrade head`, `pytest` o `uvicorn app.main:app --reload`.
#
# Por que hace falta: WSL2 apaga la VM ~8s despues de que termina el ultimo
# proceso que la uso. `postgresql` esta habilitado como servicio (systemd),
# pero eso solo lo arranca automaticamente CUANDO la VM de WSL ya esta viva.
# Este script la deja viva en segundo plano para que Postgres siga escuchando
# en localhost:5432 (WSL2 reenvia ese puerto a Windows automaticamente).

$existing = Get-Process wsl -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process wsl.exe -ArgumentList "-d Ubuntu -u root -e sleep infinity" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

wsl -d Ubuntu -u root -e bash -c "service postgresql status" | Out-Null
if ($LASTEXITCODE -ne 0) {
    wsl -d Ubuntu -u root -e bash -c "service postgresql start"
}

$ok = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue
if ($ok.TcpTestSucceeded) {
    Write-Host "Postgres listo en localhost:5432" -ForegroundColor Green
} else {
    Write-Host "No se pudo confirmar conexion a Postgres. Revisa 'wsl -d Ubuntu -u root -e service postgresql status'" -ForegroundColor Red
}
