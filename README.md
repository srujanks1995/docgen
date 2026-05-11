# docgen

Generate a Word report (`.docx`) from:
- a **ZIP bundle** containing `bundle.json` + `tables/` + `images/` (+ optional `content/` and `.docx` math snippets)
- a **YAML template** describing the document structure

The generator also writes **`llm_out.txt`** (prompt + inputs + responses) next to the output document by default.

## Word template (header/footer)

If the ZIP bundle contains **exactly one `.docx` file**, `docgen` uses it as the **base template** (so the report keeps the template’s **header and footer**). Any body content inside the template is cleared before writing the generated report content.

## How to run (example report ~10 pages)

From the repo root:

```powershell
python -m pip install -e ".[dev]"
python tools\build_example.py
python -m docgen --zip example\report.zip --yaml example\report.yaml --out example\report.docx
```

Outputs:
- `example\report.docx`
- `example\llm_out.txt`

## LLM configuration (`.env`)

Create/update `.env` in the repo root (it is gitignored).

### Option A: Google Gemini API (default)

```ini
DOCGEN_LLM_SOURCE=api
GEMINI_API_KEY=YOUR_KEY
```

Optional:

```ini
GEMINI_MODEL=gemini-2.0-flash
GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta
```

### Option B: Local model command (model path + adapter path)

`docgen` can call a **local command** that reads a JSON request from stdin and prints the generated text to stdout.

```ini
DOCGEN_LLM_SOURCE=local
DOCGEN_LOCAL_LLM_COMMAND=python tools/local_llm_runner.py
DOCGEN_LOCAL_MODEL_PATH=C:\models\my-model.gguf
DOCGEN_LOCAL_ADAPTER_PATH=C:\models\my-adapter.safetensors
```

Notes:
- `DOCGEN_LOCAL_ADAPTER_PATH` is optional (use it for fine-tuned/LoRA adapters).
- The local command receives these environment variables:
  - `DOCGEN_LOCAL_MODEL_PATH`
  - `DOCGEN_LOCAL_ADAPTER_PATH`

### No LLM (stub text)

```powershell
python -m docgen --zip example\report.zip --yaml example\report.yaml --out example\report.docx --no-llm
```

## LLM trace file (`llm_out.txt`)

By default, `docgen` writes `llm_out.txt` next to `--out`.

You can override or disable it:

```powershell
python -m docgen --zip example\report.zip --yaml example\report.yaml --out example\report.docx --llm-log example\my_trace.txt
python -m docgen --zip example\report.zip --yaml example\report.yaml --out example\report.docx --no-llm-log
```

