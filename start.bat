@echo off
rem Video Studio Local: chay server roi tu mo trinh duyet. Dong cua so nay = tat app.
rem Chay bang cach BAM DUP trong File Explorer (bam trong VS Code chi mo file de sua).
rem File nay chi dung ky tu ASCII + xuong dong CRLF de cmd.exe doc dung.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1

if not exist ".venv\Scripts\python.exe" (
  echo Khong tim thay .venv - xem README.md phan "Cai dat".
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m app.main --open
pause
