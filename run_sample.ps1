# Build the example ZIP and generate the Word report (+ llm_out.txt trace).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -e ".[dev]" -q
python tools\build_example.py
python -m docgen `
  --zip example\report.zip `
  --yaml example\report.yaml `
  --out example\report.docx

Write-Host "Done: example\report.docx and example\llm_out.txt"
