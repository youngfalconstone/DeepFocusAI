@echo off
rem Undo everything DeepFocus AI did (hosts block, hidden files, scheduled task) from outside the app.
rem Windows will show a UAC prompt. The app itself has the same action under Settings > Emergency restore.
cd /d "%~dp0"
if exist "dist\DeepFocus\DeepFocus.exe" (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -Wait -FilePath 'dist\DeepFocus\DeepFocus.exe' -ArgumentList 'helper','restore','--reason','manual'"
) else (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -Wait -FilePath 'python' -ArgumentList '-m','deepfocus.helper','restore','--reason','manual'"
)
echo Done. Check %USERPROFILE%\.deepfocus\deepfocus.log for details.
pause
