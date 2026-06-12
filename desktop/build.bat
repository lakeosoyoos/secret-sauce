@echo off
REM ===================================================================
REM  Build SecretSauce.exe on Windows.
REM  Prereq: Python 3.11 or 3.12 installed (64-bit), on PATH.
REM  Run this from the SecretSauce-Desktop folder:  build.bat
REM ===================================================================

echo.
echo === Creating a clean build environment ===
python -m venv build_env
call build_env\Scripts\activate.bat

echo.
echo === Installing dependencies ===
python -m pip install --upgrade pip
REM setuptools is pinned FIRST (65.5.1) so its lenient pkg_resources is what
REM the build sees — newer setuptools makes the packaged app crash at launch.
python -m pip install -r requirements-desktop.txt

echo.
echo === Building SecretSauce.exe (this takes several minutes) ===
pyinstaller SecretSauce.spec --noconfirm --clean

echo.
echo === Done ===
echo The app is in:  dist\SecretSauce\SecretSauce.exe
echo Zip the dist\SecretSauce folder to hand to a tech.
echo A tech extracts it and double-clicks SecretSauce.exe.
echo.
pause
