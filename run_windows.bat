@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ===========================================================
echo  GERADOR DE DDS 1.2.2 - Modelo institucional e lista por turno
echo ===========================================================

rem Windows 7 legado: preferir Python 3.8 de 32 bits.
set DDS_PY=
set DDS_VENV=
where py >nul 2>&1
if not errorlevel 1 (
  py -3.8-32 -c "import sys,struct; sys.exit(0 if sys.version_info[:2]==(3,8) and struct.calcsize('P')*8==32 else 1)" >nul 2>&1
  if not errorlevel 1 (
    set DDS_VENV=.venv38
    if not exist .venv38\Scripts\python.exe py -3.8-32 -m venv .venv38
    if errorlevel 1 goto erro_venv
    goto runtime_ok
  )
  rem Windows 10/11: Python 3.11 e ambiente separado.
  py -3.11 -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set DDS_VENV=.venv311
    if not exist .venv311\Scripts\python.exe py -3.11 -m venv .venv311
    if errorlevel 1 goto erro_venv
    goto runtime_ok
  )
)

rem Fallback quando o Python Launcher nao esta instalado.
where python >nul 2>&1
if errorlevel 1 goto erro_python
python -c "import sys,struct; sys.exit(0 if sys.version_info[:2]==(3,8) and struct.calcsize('P')*8==32 else 1)" >nul 2>&1
if not errorlevel 1 (
  set DDS_VENV=.venv38
  if not exist .venv38\Scripts\python.exe python -m venv .venv38
  if errorlevel 1 goto erro_venv
  goto runtime_ok
)
python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set DDS_VENV=.venv311
  if not exist .venv311\Scripts\python.exe python -m venv .venv311
  if errorlevel 1 goto erro_venv
  goto runtime_ok
)
goto erro_python

:runtime_ok
set DDS_PY=%DDS_VENV%\Scripts\python.exe
"%DDS_PY%" -c "import sys; print('Python selecionado:',sys.version)"
if not exist "%DDS_PY%" goto erro_venv
if exist "%DDS_VENV%\.dds-deps-ready" goto deps_ready
echo Instalando dependencias compativeis. Aguarde...
"%DDS_PY%" -m pip install --upgrade "pip<25.1"
if errorlevel 1 goto erro_deps
"%DDS_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto erro_deps
echo ok>"%DDS_VENV%\.dds-deps-ready"
:deps_ready

set DDS_DATA_DIR=%LOCALAPPDATA%\GrupoGPS\PlataformaDDS
if not exist "%DDS_DATA_DIR%" mkdir "%DDS_DATA_DIR%"
set DDS_PORT=8766

"%DDS_PY%" -c "import json,urllib.request,sys; d=json.loads(urllib.request.urlopen('http://127.0.0.1:8766/api/health',timeout=1).read().decode('utf-8')); sys.exit(0 if d.get('version')=='1.2.2-local' else 2)" >nul 2>&1
if not errorlevel 1 goto abrir
"%DDS_PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/api/health',timeout=1).read()" >nul 2>&1
if not errorlevel 1 goto erro_porta
if exist dds_startup.log del dds_startup.log
start "Gerador de DDS - servico local" /min cmd /c ""%DDS_PY%" -u app.py > dds_startup.log 2>&1"
"%DDS_PY%" check_server.py
if errorlevel 1 goto erro

:abrir
start "" "http://127.0.0.1:8766"
echo.
echo Gerador de DDS iniciado e verificado.
echo Endereco local: http://127.0.0.1:8766
echo Dados locais: %DDS_DATA_DIR%
echo Diagnostico: dds_startup.log
echo.
pause
goto fim

:erro_python
echo.
echo Python compativel nao encontrado.
echo Windows 7: Python 3.8 de 32 bits. Windows 10/11: Python 3.11.
echo Instale somente uma versao autorizada pela TI e tente novamente.
echo.
pause
goto fim
:erro_venv
echo.
echo Nao foi possivel criar o ambiente Python. Verifique permissoes e espaco em disco.
echo.
pause
goto fim
:erro_deps
echo.
echo Falha ao instalar dependencias. Verifique internet, permissao da TI e dds_startup.log.
echo.
pause
goto fim
:erro_porta
echo.
echo A porta 8766 esta ocupada por outro servico ou versao do DDS. Feche-o e tente novamente.
echo.
pause
goto fim
:erro
echo.
echo Falha ao iniciar o Gerador de DDS. Diagnostico:
if exist dds_startup.log type dds_startup.log
echo.
pause
:fim
endlocal
