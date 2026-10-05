@echo off
setlocal EnableDelayedExpansion

rem Run mask_filling_trial4.py in the dedicated WSL Python environment.
set "REPO_DIR=%~dp0"
set "INPUT_CSV=%~1"
set "OUTPUT_DIR=%~2"
set "MAX_ROWS=%~3"
set "AUGMENTATION_FACTOR=%~4"
set "TOP_K=%~5"
set "CHANGE_SEX=%~6"

if not defined OUTPUT_DIR set "OUTPUT_DIR=mask_filling_outputs"
if not defined AUGMENTATION_FACTOR set "AUGMENTATION_FACTOR=5"
if not defined TOP_K set "TOP_K=12"
if not defined CHANGE_SEX set "CHANGE_SEX=true"

rem When omitted, use the newest CSV produced by run_mask_trial3.bat.
if not defined INPUT_CSV (
    for /f "delims=" %%F in ('dir /b /a-d /o-d "%REPO_DIR%mask_trial_outputs\clinical_case_mask_trial3_*.csv" 2^>nul') do (
        if not defined INPUT_CSV set "INPUT_CSV=mask_trial_outputs/%%F"
    )
)

if not defined INPUT_CSV (
    echo [ERROR] No mask-trial3 CSV was found.
    echo [ERROR] Run run_mask_trial3.bat first or specify an input CSV.
    exit /b 2
)

set "CHANGE_SEX_ARG="
if /i "%CHANGE_SEX%"=="true" set "CHANGE_SEX_ARG=--change-sex"
if /i "%CHANGE_SEX%"=="yes" set "CHANGE_SEX_ARG=--change-sex"
if "%CHANGE_SEX%"=="1" set "CHANGE_SEX_ARG=--change-sex"

echo [INFO] Repository          : %REPO_DIR%
echo [INFO] Input CSV           : %INPUT_CSV%
echo [INFO] Output dir          : %OUTPUT_DIR%
echo [INFO] Augmentation factor : %AUGMENTATION_FACTOR%
echo [INFO] Top K               : %TOP_K%
echo [INFO] Change sex          : %CHANGE_SEX%

if defined MAX_ROWS (
    echo [INFO] Max rows            : %MAX_ROWS%
    wsl.exe --cd "%REPO_DIR%" bash -lc "source ~/.venvs/maskfill_env/bin/activate && python src/mask_filling_trial4.py --input-csv '%INPUT_CSV%' --output-dir '%OUTPUT_DIR%' --model-name emilyalsentzer/Bio_ClinicalBERT --device cuda --max-rows '%MAX_ROWS%' --augmentation-factor '%AUGMENTATION_FACTOR%' --top-k '%TOP_K%' %CHANGE_SEX_ARG%"
) else (
    wsl.exe --cd "%REPO_DIR%" bash -lc "source ~/.venvs/maskfill_env/bin/activate && python src/mask_filling_trial4.py --input-csv '%INPUT_CSV%' --output-dir '%OUTPUT_DIR%' --model-name emilyalsentzer/Bio_ClinicalBERT --device cuda --augmentation-factor '%AUGMENTATION_FACTOR%' --top-k '%TOP_K%' %CHANGE_SEX_ARG%"
)

if errorlevel 1 (
    echo [ERROR] Mask-Filling trial4 processing failed.
    exit /b 1
)

echo [INFO] Mask-Filling trial4 processing completed.
exit /b 0
