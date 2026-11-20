@echo off
rem Starts TalkSubs: the local server and its window. Closing the window stops everything.
rem The app sets its own environment (caches and temp files stay in this folder); see parakeet_stt\env.py.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo TalkSubs is not installed in this folder yet ^(.venv is missing^).
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m parakeet_stt app
