@echo off
setlocal

rem Run mask_trial3.py in the dedicated WSL Python environment.
set "REPO_DIR=%~dp0"
set "INPUT_CSV=%~1"
set "OUTPUT_DIR=%~2"
set "MAX_ROWS=%~3"

if not defined INPUT_CSV set "INPUT_CSV=example/karte_example.csv"
if not defined OUTPUT_DIR set "OUTPUT_DIR=mask_trial_outputs"

echo [INFO] Repository : %REPO_DIR%
echo [INFO] Input CSV  : %INPUT_CSV%
echo [INFO] Output dir : %OUTPUT_DIR%

if defined MAX_ROWS (
    echo [INFO] Max rows   : %MAX_ROWS%
    wsl.exe --cd "%REPO_DIR%" bash -lc "source ~/.venvs/maskfill_env/bin/activate && python src/mask_trial3.py --input-csv '%INPUT_CSV%' --output-dir '%OUTPUT_DIR%' --target-column 'Case presentation EN' --max-rows '%MAX_ROWS%'"
) else (
    wsl.exe --cd "%REPO_DIR%" bash -lc "source ~/.venvs/maskfill_env/bin/activate && python src/mask_trial3.py --input-csv '%INPUT_CSV%' --output-dir '%OUTPUT_DIR%' --target-column 'Case presentation EN'"
)

if errorlevel 1 (
    echo [ERROR] Mask trial3 processing failed.
    exit /b 1
)

echo [INFO] Mask trial3 processing completed.
exit /b 0
