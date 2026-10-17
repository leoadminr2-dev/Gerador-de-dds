@echo off
setlocal
cd /d "%~dp0"
echo ===========================================================
echo  GERADOR DE DDS - Inicializador para Windows 8 / 8.1
echo  Este atalho chama o inicializador principal compartilhado.
echo ===========================================================
call "%~dp0run_windows.bat"
endlocal
