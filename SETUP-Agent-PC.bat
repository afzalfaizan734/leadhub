@echo off
setlocal
title LeadHub - stop the "Open RingCentral?" popup

rem --- self-elevate: no need to right-click, just approve the prompt -----------
net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo.
  echo   Asking for administrator permission...
  echo   Click YES on the Windows prompt.
  echo.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
  exit /b
)

cls
echo.
echo   ============================================================
echo     LeadHub - stop the "Open RingCentral?" popup
echo     Running as administrator. This takes a few seconds.
echo   ============================================================
echo.

rem Restricted to the LeadHub app origins only (never "*", which would let ANY
rem website silently launch the RingCentral handler on this PC).
set JSON=[{\"protocol\":\"rcapp\",\"allowed_origins\":[\"https://app.championlogisticworldwideinc.online\",\"http://leadmanager\",\"http://localhost\"]}]

echo   [1/3] Allowing RingCentral dial links in Chrome and Edge...
reg add "HKLM\SOFTWARE\Policies\Google\Chrome" /v AutoLaunchProtocolsFromOrigins /t REG_SZ /d "%JSON%" /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Edge"  /v AutoLaunchProtocolsFromOrigins /t REG_SZ /d "%JSON%" /f >nul 2>&1

echo   [2/3] Closing browsers so the setting takes effect...
taskkill /IM chrome.exe /F >nul 2>&1
taskkill /IM msedge.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo   [3/3] Checking it saved...
set OKC=no
set OKE=no
reg query "HKLM\SOFTWARE\Policies\Google\Chrome" /v AutoLaunchProtocolsFromOrigins >nul 2>&1 && set OKC=yes
reg query "HKLM\SOFTWARE\Policies\Microsoft\Edge" /v AutoLaunchProtocolsFromOrigins >nul 2>&1 && set OKE=yes

echo.
echo   ------------------------------------------------------------
if "%OKC%"=="yes" (echo     Chrome : DONE) else (echo     Chrome : FAILED)
if "%OKE%"=="yes" (echo     Edge   : DONE) else (echo     Edge   : FAILED)
echo   ------------------------------------------------------------
echo.

if "%OKC%"=="yes" (
  echo     All set. Open Chrome, go to  http://leadmanager
  echo     and click a phone number - it should dial straight away.
  echo.
  echo     If it still asks, close Chrome COMPLETELY from the
  echo     system tray near the clock, then open it again.
) else (
  echo     It did not save. Tell your admin and show them this window.
)
echo.
echo   Press any key to close.
pause >nul
