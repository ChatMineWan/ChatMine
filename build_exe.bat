@echo off
echo Building ChatMine...
echo.

echo Installing pyinstaller...
pip install pyinstaller -q
echo.

echo Packaging to EXE (may take 3-5 minutes)...
pyinstaller --noconfirm --onefile --windowed --name ChatMine --icon icon.ico app.py
echo.

echo Done! EXE is in dist\ChatMine.exe
pause
