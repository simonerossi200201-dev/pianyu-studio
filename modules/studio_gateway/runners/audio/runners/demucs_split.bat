@echo off
rem Demucs (Meta, MIT) vocals/music split. Usage: demucs_split.bat input.(wav|mp3|mp4) [model: htdemucs^|htdemucs_ft] [outdir]
set TORCH_HOME=D:\0_LLM\audio\models\torch
set M=%2& if "%M%"=="" set M=htdemucs_ft
set O=%3& if "%O%"=="" set O=D:\0_LLM\Outputs\demucs
D:\0_LLM\audio\venv_demucs\Scripts\python.exe -m demucs -n %M% --two-stems vocals -d cuda -o "%O%" "%~1"
