@echo off
rem Abre Sync Master. Si el equipo no tiene Python, ofrece instalarlo con winget.
rem Lo demas (numpy, scipy, ffmpeg) lo descarga el propio programa la primera vez que se abre.
setlocal
set "GUI=%~dp0Sincronizar_Doblaje_Gui.py"
set "PYW="

rem 1) Lanzador "py" de una instalacion normal de python.org
py -3 -c "import tkinter" >nul 2>&1 && set "PYW=pyw -3"
if defined PYW goto lanzar

rem 2) "python" en el PATH (el alias de la Microsoft Store no imprime nada y se descarta)
for /f "delims=" %%i in ('python -c "import os, sys, tkinter; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2^>nul') do set "PYW="%%i""
if defined PYW goto lanzar

rem 3) Instalacion por usuario en la ruta habitual
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW="%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe""
if defined PYW goto lanzar

echo No se ha encontrado Python 3 en este equipo; hace falta para abrir el programa.
where winget >nul 2>&1 || goto manual
choice /c SN /m "Instalar Python 3.12 ahora con winget"
if errorlevel 2 goto manual
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (
    set "PYW="%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe""
    goto lanzar
)
echo No se pudo instalar Python automaticamente.

:manual
echo Instala Python 3 desde https://www.python.org/downloads/ y vuelve a abrir este archivo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:lanzar
start "" %PYW% "%GUI%" %*
exit /b 0
