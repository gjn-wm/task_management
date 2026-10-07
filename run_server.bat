@echo off
cd /d "C:\Users\warr\Desktop\dev_gjn\Task_itms"

rem Launched by the "Task Managment system" scheduled task at boot (no console).
rem stdout/stderr go to logs\server.log since there is no terminal to attach to.
if not exist "logs" mkdir "logs"
echo. >> "logs\server.log"
echo ==== start %DATE% %TIME% ==== >> "logs\server.log"

"C:\Users\warr\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\warr\Desktop\dev_gjn\Task_itms\server.py" >> "logs\server.log" 2>&1

echo ==== exited with %ERRORLEVEL% at %DATE% %TIME% ==== >> "logs\server.log"
exit /b %ERRORLEVEL%
