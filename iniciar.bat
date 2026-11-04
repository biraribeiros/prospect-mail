@echo off
REM Inicia o Prospector com um duplo clique (precisa do Docker Desktop aberto).
cd /d "%~dp0"
if not exist .env (
  echo Arquivo .env nao encontrado. Copie .env.example para .env e preencha. Veja docs/COMO_USAR.md
  pause
  exit /b 1
)
docker compose up -d --build
echo.
echo Painel:  http://localhost:8000
echo Emails de teste (Mailpit):  http://localhost:8025
start http://localhost:8000
pause
