@echo off
title LeadHub - name this PC "leadmanager"
echo.
echo  ============================================================
echo    This renames THIS PC to  LEADMANAGER.
echo.
echo    After the restart, Saad and Ahmad simply open:
echo         http://leadmanager
echo.
echo    It keeps working no matter how often your router changes
echo    this PC's IP address - nothing to edit, ever.
echo.
echo    Run this ONCE, on THIS PC only, as administrator.
echo    The PC WILL RESTART at the end - save your work first.
echo  ============================================================
echo.

net session >nul 2>&1
if not %errorlevel%==0 (
  echo  ERROR: not running as administrator.
  echo  Right-click this file and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo  Current name: %COMPUTERNAME%
echo.
set /p GO=  Rename to LEADMANAGER and restart now? (Y/N):
if /i not "%GO%"=="Y" (
  echo.
  echo  Cancelled - nothing was changed.
  pause
  exit /b 0
)

echo.
echo  Removing the old SMB-only alias (it never worked for browsers)...
reg delete "HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters" /v OptionalNames /f >nul 2>&1

echo  Opening the firewall for LeadHub...
netsh advfirewall firewall delete rule name="LeadHub (TCP 80)" >nul 2>&1
netsh advfirewall firewall add rule name="LeadHub (TCP 80)" dir=in action=allow protocol=TCP localport=80 profile=private >nul
netsh advfirewall firewall delete rule name="LeadHub (TCP 8000)" >nul 2>&1
netsh advfirewall firewall add rule name="LeadHub (TCP 8000)" dir=in action=allow protocol=TCP localport=8000 profile=private >nul

echo  Renaming the computer...
powershell -NoProfile -Command "Rename-Computer -NewName 'LEADMANAGER' -Force -ErrorAction Stop"
if not %errorlevel%==0 goto failed

echo.
echo  ============================================================
echo    Renamed. Restarting in 15 seconds...
echo.
echo    After the restart, start LeadHub and tell the team:
echo         http://leadmanager
echo  ============================================================
echo.
shutdown /r /t 15 /c "LeadHub: renaming this PC to LEADMANAGER"
pause
exit /b 0

:failed
echo.
echo  RENAME FAILED. Nothing was restarted.
echo  Your PC is still called %COMPUTERNAME% and everything still works
echo  at  http://%COMPUTERNAME%
echo.
pause
