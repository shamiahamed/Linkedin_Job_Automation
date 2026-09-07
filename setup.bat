@echo off
CALL  Running Job Auto-Apply setup...
echo.

echo ========================================
echo  JOB AUTO-APPLY - INSTALLER
echo ========================================
echo.

echo [1/4] Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    exit /b 1
)

echo.
echo [2/4] Checking Tesseract OCR...
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo Tesseract found.
) else (
    echo.
    echo  !!! TESSERACT NOT FOUND - OCR feature disabled !!!
    echo  Download from: https://github.com/UB-Mannheim/tesseract/wiki
    echo  Install to:    C:\Program Files\Tesseract-OCR
    echo.
)

echo.
echo [3/4] Setting up .env - add your BREVO_API_KEY
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from template.
    echo  - Edit .env and set BREVO_API_KEY
)

echo.
echo [4/4] Starting backend server...
echo.
echo  Dashboard will be at:  http://localhost:8000/dashboard
echo  Press Ctrl+C to stop.
echo.
cd backend
python main.py