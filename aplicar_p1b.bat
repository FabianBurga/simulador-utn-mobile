@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo P1-B - APLICACION SEGURA
echo ============================================================
echo.

echo [1/4] Verificando baseline P0...
python tools\validate_project.py

if errorlevel 1 goto ERROR

echo.
echo [2/4] Aplicando metadata de calidad...
python tools\apply_p1b_quality.py

if errorlevel 1 goto RESTORE

echo.
echo [3/4] Validando P1-B...
python tools\validate_p1b.py

if errorlevel 1 goto RESTORE

echo.
echo [4/4] Repitiendo pruebas P0...
python tools\validate_project.py

if errorlevel 1 goto RESTORE

echo.
echo ============================================================
echo P1-B APROBADO
echo ============================================================
echo Banco enriquecido sin alterar contenido original.
echo.
exit /b 0


:RESTORE
echo.
echo ERROR DURANTE P1-B.
echo Restaurando question_bank.json desde baseline P0...
copy /Y ^
"data\question_bank_p0_backup.json" ^
"data\question_bank.json" >nul

echo Banco restaurado.
goto ERROR


:ERROR
echo.
echo ============================================================
echo P1-B RECHAZADO
echo NO CONTINUAR CON P2
echo ============================================================
echo.
exit /b 1