"""
DocGen API server — run from the project root:

    pip install fastapi uvicorn[standard] python-multipart
    uvicorn server:app --port 8000

Then open http://localhost:8000
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent
EXAMPLE_DIR = ROOT / "example"
YAML_DIR    = ROOT / "yaml"
OUT_DIR     = ROOT / "out"

EXAMPLE_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

# ── Job store ─────────────────────────────────────────────────────────────────
class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


class Job:
    def __init__(self, job_id: str, zip_name: str, param: str):
        self.job_id      = job_id
        self.zip_name    = zip_name
        self.param       = param
        self.status      = Status.PENDING
        self.created_at  = datetime.utcnow().isoformat() + "Z"
        self.finished_at: str | None = None
        self.log_lines: list[str] = []
        self.out_path:  Path | None = None
        # Queue used to push new lines to the SSE stream; None = sentinel (done)
        self._q: queue.Queue[str | None] = queue.Queue()

    def push(self, line: str) -> None:
        self.log_lines.append(line)
        self._q.put(line)

    def close(self) -> None:
        self._q.put(None)


JOBS: dict[str, Job] = {}

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="DocGen", docs_url="/api/docs", redoc_url=None)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _yamls() -> list[str]:
    return sorted(p.stem for p in YAML_DIR.glob("*.yaml"))


def _resolve_yaml(param: str) -> Path:
    stem = param.removesuffix(".yaml")
    path = YAML_DIR / f"{stem}.yaml"
    if not path.is_file():
        raise HTTPException(404, f"YAML '{param}' not found. Available: {_yamls()}")
    return path


def _auto_yaml(zip_name: str) -> Path:
    """
    Select the exact YAML file based on Case_N.zip filename.

    Mapping (from image — yaml folder must contain these files):
      Case_1  -> static.yaml
      Case_2  -> static-acceleration.yaml
      Case_3  -> static-acceleration-solution_combination.yaml
      Case_4  -> combined-static-acceleration-solution_combination.yaml
      Case_5  -> static-solution_combination.yaml
      Case_6  -> combined-static.yaml
      Case_7  -> combined-static-acceleration.yaml
      Case_8  -> combined-static-solution_combination.yaml
      Case_9  -> static-acceleration.yaml
      Case_10 -> combined-static-acceleration.yaml
      Case_11 -> harmonic.yaml
    """
    import re as _re
    CASE_YAML = {
        1:  "static",
        2:  "static-acceleration",
        3:  "static-acceleration-solution_combination",
        4:  "combined-static-acceleration-solution_combination",
        5:  "static-solution_combination",
        6:  "combined-static",
        7:  "combined-static-acceleration",
        8:  "combined-static-solution_combination",
        9:  "static-acceleration",
        10: "combined-static-acceleration",
        11: "harmonic",
    }
    m = _re.search(r"case[_\-]?(\d+)", zip_name, _re.IGNORECASE)
    if m:
        case_num = int(m.group(1))
        stem = CASE_YAML.get(case_num)
        if stem is None:
            raise HTTPException(400, f"No YAML mapping for Case_{case_num}. Supported: 1–11.")
    else:
        raise HTTPException(400, f"Could not determine case number from filename '{zip_name}'. Expected format: Case_N.zip")

    path = YAML_DIR / f"{stem}.yaml"
    if not path.is_file():
        raise HTTPException(500, f"Expected YAML '{stem}.yaml' not found in {YAML_DIR}")
    return path


def _run_sync(job: Job, zip_path: Path, yaml_path: Path, out_path: Path) -> None:
    """Run docgen in a real subprocess (synchronous, works on Windows too)."""
    cmd = [
        sys.executable, "-m", "docgen",
        "--zip",  str(zip_path),
        "--yaml", str(yaml_path),
        "--out",  str(out_path),
        "--no-llm",
        "--no-llm-log",
    ]
    job.status = Status.RUNNING
    job.push(f"$ {' '.join(cmd)}")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"   # force UTF-8 stdout on Windows

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        for line in proc.stdout:
            job.push(line.rstrip())

        proc.wait()

        if proc.returncode == 0 and out_path.is_file():
            job.status   = Status.DONE
            job.out_path = out_path
            job.push("\u2713 Done")
        else:
            job.status = Status.FAILED
            job.push(f"\u2717 Process exited with code {proc.returncode}")

    except Exception as exc:
        job.status = Status.FAILED
        job.push(f"\u2717 Exception: {exc}")
        for ln in traceback.format_exc().splitlines():
            job.push(f"  {ln}")
    finally:
        job.finished_at = datetime.utcnow().isoformat() + "Z"
        job.close()


def _stream_job(job: Job) -> Iterator[str]:
    """Pull lines from the job queue and yield SSE events."""
    while True:
        line = job._q.get()
        if line is None:
            break
        yield f"data: {line}\n\n"

    tag = "DONE" if job.status == Status.DONE else "FAILED"
    yield f"data: {tag}:{job.job_id}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui():
    return HTMLResponse(_HTML)


@app.get("/api/yamls")
def list_yamls():
    return {"yamls": _yamls()}


@app.post("/api/generate")
def generate(
    file: UploadFile = File(...),
):
    safe     = Path(file.filename or "upload.zip").name
    zip_dest = EXAMPLE_DIR / safe
    with zip_dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    yaml_path = _auto_yaml(safe)
    param     = yaml_path.stem   # e.g. "combined_report"

    job_id   = str(uuid.uuid4())
    out_path = OUT_DIR / f"report_{job_id}.docx"
    job      = Job(job_id=job_id, zip_name=safe, param=param)
    JOBS[job_id] = job

    # Run docgen in a background thread (no async subprocess needed)
    t = threading.Thread(
        target=_run_sync,
        args=(job, zip_dest, yaml_path, out_path),
        daemon=True,
    )
    t.start()

    return StreamingResponse(
        _stream_job(job),
        media_type="text/event-stream",
        headers={"X-Job-Id": job_id, "Cache-Control": "no-cache"},
    )


@app.get("/api/report/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in (Status.PENDING, Status.RUNNING):
        raise HTTPException(425, "Job not finished yet")
    if job.status == Status.FAILED:
        raise HTTPException(500, f"Job failed. Last log lines: {job.log_lines[-5:]}")
    if not job.out_path or not job.out_path.is_file():
        raise HTTPException(500, "Output file missing")
    return FileResponse(
        str(job.out_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="report.docx",
    )


# ── Embedded UI ───────────────────────────────────────────────────────────────
_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DocGen</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#0f1117;color:#e2e8f0;min-height:100vh;
     display:flex;flex-direction:column;align-items:center;padding:48px 16px 80px}

.wrap{width:100%;max-width:640px;display:flex;flex-direction:column;gap:20px}

h1{font-size:1.6rem;font-weight:700;letter-spacing:-.5px;text-align:center}
h1 em{color:#6c63ff;font-style:normal}
.sub{text-align:center;color:#475569;font-size:.88rem;margin-top:4px}

.card{background:#1a1d27;border:1px solid #2a2d3e;border-radius:12px;padding:24px;
      display:flex;flex-direction:column;gap:16px}

.drop{border:2px dashed #2a2d3e;border-radius:8px;padding:36px 16px;
      text-align:center;cursor:pointer;transition:.2s;position:relative}
.drop:hover,.drop.over{border-color:#6c63ff;background:rgba(108,99,255,.05)}
.drop input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.drop-icon{font-size:2rem;margin-bottom:8px}
.drop-hint{color:#475569;font-size:.88rem}
.drop-hint b{color:#6c63ff}
.drop-file{margin-top:8px;font-size:.8rem;font-family:monospace;
           background:rgba(108,99,255,.12);display:inline-block;
           padding:3px 10px;border-radius:6px;color:#c4b5fd}



button{width:100%;padding:12px;background:#6c63ff;border:none;border-radius:8px;
       color:#fff;font-size:.95rem;font-weight:600;cursor:pointer;transition:.18s}
button:hover:not(:disabled){background:#5a52e0}
button:disabled{opacity:.4;cursor:not-allowed}

#prog{display:none;flex-direction:column;gap:12px}
.status-row{display:flex;align-items:center;gap:10px;font-size:.88rem}
.dot{width:9px;height:9px;border-radius:50%;background:#475569;flex-shrink:0}
.dot.running{background:#f59e0b;animation:blink 1s infinite}
.dot.done{background:#22c55e}
.dot.failed{background:#ef4444}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

.log{background:#08090f;border:1px solid #1e2130;border-radius:8px;
     padding:12px 14px;max-height:300px;overflow-y:auto;
     font-family:"JetBrains Mono","Fira Code",monospace;font-size:.76rem;
     line-height:1.75;color:#64748b}
.log .ok  {color:#22c55e}
.log .err {color:#ef4444}
.log .warn{color:#f59e0b}

#dlBtn{display:none;width:100%;padding:12px;background:#22c55e;border:none;
       border-radius:8px;color:#0a0f0a;font-size:.95rem;font-weight:700;
       cursor:pointer;text-decoration:none;text-align:center;transition:.18s}
#dlBtn:hover{filter:brightness(1.08)}
#dlBtn.show{display:block}
</style>
</head>
<body>
<div class="wrap">

  <div>
    <h1>Doc<em>Gen</em></h1>
    <p class="sub">Upload a ZIP bundle · template is auto-selected · download your report</p>
  </div>

  <div class="card">
    <div class="drop" id="drop">
      <input type="file" id="fileIn" accept=".zip">
      <div class="drop-icon">📦</div>
      <div class="drop-hint">Drop your <b>.zip</b> here or click to browse</div>
      <div class="drop-file" id="fname" style="display:none"></div>
    </div>

    <button id="runBtn" disabled>Generate Report</button>
  </div>

  <div class="card" id="prog">
    <div class="status-row">
      <div class="dot" id="dot"></div>
      <span id="statusTxt">Starting…</span>
    </div>
    <div class="log" id="log"></div>
    <a id="dlBtn" href="#" download="report.docx">⬇ Download report.docx</a>
  </div>

</div>
<script>
const fileIn       = document.getElementById('fileIn');
const drop         = document.getElementById('drop');
const fname        = document.getElementById('fname');
const runBtn       = document.getElementById('runBtn');
const prog         = document.getElementById('prog');
const dot       = document.getElementById('dot');
const statusTxt = document.getElementById('statusTxt');
const log       = document.getElementById('log');
const dlBtn     = document.getElementById('dlBtn');

function inferYaml(name) {
  const caseMap = {
    1:  'static',
    2:  'static-acceleration',
    3:  'static-acceleration-solution_combination',
    4:  'combined-static-acceleration-solution_combination',
    5:  'static-solution_combination',
    6:  'combined-static',
    7:  'combined-static-acceleration',
    8:  'combined-static-solution_combination',
    9:  'static-acceleration',
    10: 'combined-static-acceleration',
    11: 'harmonic',
  };
  const m = name.match(/[Cc]ase[_-]?(\d+)/);
  if (m) {
    const n = parseInt(m[1], 10);
    return (caseMap[n] || '?') + '.yaml';
  }
  return '(unknown case)';
}

function setFile(f) {
  if (!f) return;
  fname.textContent = f.name;
  fname.style.display = 'inline-block';
  runBtn.disabled = false;
}
fileIn.addEventListener('change', () => setFile(fileIn.files[0]));
drop.addEventListener('dragover',  e => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('over');
  const f = e.dataTransfer.files[0];
  if (!f?.name.endsWith('.zip')) return alert('Please drop a .zip file.');
  const dt = new DataTransfer(); dt.items.add(f);
  fileIn.files = dt.files;
  setFile(f);
});

runBtn.addEventListener('click', async () => {
  const file = fileIn.files[0];
  if (!file) return;

  runBtn.disabled = true;
  prog.style.display = 'flex';
  log.innerHTML = '';
  dlBtn.classList.remove('show');
  setState('running', 'Running\u2026');

  const fd = new FormData();
  fd.append('file', file);

  try {
    const res = await fetch('/api/generate', { method: 'POST', body: fd });
    const jobId = res.headers.get('x-job-id');

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith('data:')) continue;
        const line = part.slice(5).trim();
        if (!line) continue;
        if (line.startsWith('DONE:')) {
          setState('done', 'Complete \u2713');
          dlBtn.href = '/api/report/' + line.slice(5);
          dlBtn.classList.add('show');
          runBtn.disabled = false;
        } else if (line.startsWith('FAILED:')) {
          setState('failed', 'Generation failed \u2014 see log below');
          runBtn.disabled = false;
        } else {
          addLog(line);
        }
      }
    }
  } catch(err) {
    setState('failed', 'Network error: ' + err.message);
    runBtn.disabled = false;
  }
});

function setState(state, text) {
  dot.className = 'dot ' + state;
  statusTxt.textContent = text;
}

function addLog(line) {
  const d = document.createElement('div');
  const lo = line.toLowerCase();
  d.className = (line.includes('\u2713') || lo.includes('wrote'))           ? 'ok'
              : (lo.includes('error') || line.includes('\u2717') || lo.includes('traceback') || lo.includes('exception')) ? 'err'
              : lo.includes('warn') ? 'warn' : '';
  d.textContent = line;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
</script>
</body>
</html>"""
