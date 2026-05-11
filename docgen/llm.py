from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


@dataclass
class LLMLogContext:
    """Append-only log of Gemini calls (and stub/disabled rows) for one document build."""

    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _seq: int = 0

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def append(self, text: str) -> None:
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)


def _stub_paragraph(context: dict[str, Any], prompt: str) -> str:
    bits = ", ".join(f"{k}={v}" for k, v in context.items() if v is not None)
    return (
        f"This paragraph was generated without a Gemini API key (stub mode). "
        f"Context: {bits}. "
        f"Instruction summary: {prompt[:200]}{'…' if len(prompt) > 200 else ''}"
    )


def _gemini_text_from_response(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        raise ValueError("Gemini response has no candidates")
    parts = (cands[0].get("content") or {}).get("parts") or []
    if not parts:
        raise ValueError("Gemini response has no content parts")
    texts = [str(p.get("text", "")) for p in parts if isinstance(p, dict)]
    return "\n".join(t for t in texts if t).strip()


def _write_log_block(
    ctx: LLMLogContext,
    *,
    seq: int,
    mode: str,
    instruction_prompt: str,
    facts_json: dict[str, Any],
    model: str,
    api_base: str,
    request_body: dict[str, Any] | None,
    http_status: int | None,
    response_json: dict[str, Any] | None,
    extracted_text: str,
    error_message: str | None,
) -> None:
    lines = [
        "=" * 80,
        f"CALL #{seq}  {datetime.now(timezone.utc).isoformat()}",
        f"MODE: {mode}",
        f"Model: {model}",
        f"API base: {api_base}",
        "",
        "--- Instruction prompt (full, after any cross-reference hints) ---",
        instruction_prompt,
        "",
        "--- facts_json (input bundled with instruction) ---",
        json.dumps(facts_json, ensure_ascii=False, indent=2),
        "",
    ]
    if request_body is not None:
        lines.extend(
            [
                "--- Request body (JSON) ---",
                json.dumps(request_body, ensure_ascii=False, indent=2),
                "",
            ]
        )
    if http_status is not None:
        lines.append(f"--- HTTP status ---\n{http_status}\n")
    if error_message:
        lines.extend(["--- Error ---", error_message, ""])
    if response_json is not None:
        lines.extend(
            [
                "--- Google API response (raw JSON) ---",
                json.dumps(response_json, ensure_ascii=False, indent=2),
                "",
            ]
        )
    lines.extend(
        [
            "--- Text used in the Word document ---",
            extracted_text if extracted_text else "(empty)",
            "",
        ]
    )
    ctx.append("\n".join(lines))


def log_paragraph_disabled(
    ctx: LLMLogContext,
    *,
    instruction_prompt: str,
    facts_json: dict[str, Any],
    body: str,
) -> None:
    seq = ctx.next_seq()
    _write_log_block(
        ctx,
        seq=seq,
        mode="llm_disabled_cli",
        instruction_prompt=instruction_prompt,
        facts_json=facts_json,
        model=os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash",
        api_base=(
            os.environ.get("GEMINI_API_BASE") or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/"),
        request_body=None,
        http_status=None,
        response_json=None,
        extracted_text=body,
        error_message="--no-llm: no request was sent to Google.",
    )


def generate_paragraph_sync(
    prompt: str,
    json_context: dict[str, Any],
    *,
    model: str | None = None,
    base_url: str | None = None,
    log_ctx: LLMLogContext | None = None,
) -> str:
    source = (os.environ.get("DOCGEN_LLM_SOURCE") or "api").strip().lower()
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GENAI_API_KEY")
    )
    root = (
        base_url
        or os.environ.get("GEMINI_API_BASE")
        or "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    m = model or os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
    seq = log_ctx.next_seq() if log_ctx else 0

    system = (
        "You write concise technical prose for formal reports. "
        "Use the provided JSON facts only as grounding; do not invent specifications. "
        "When referring to numbered figures or tables mentioned in the user message, "
        "use the exact labels provided (e.g., 'Figure 1', 'Table 2'). "
        "Aim for roughly six to ten sentences unless the instruction asks for less."
    )
    user_payload = {
        "instruction": prompt,
        "facts_json": json_context,
    }
    user_text = json.dumps(user_payload, ensure_ascii=False)

    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.4},
    }

    if source == "local":
        cmd_raw = (os.environ.get("DOCGEN_LOCAL_LLM_COMMAND") or "").strip()
        local_model_path = (os.environ.get("DOCGEN_LOCAL_MODEL_PATH") or "").strip()
        local_adapter_path = (os.environ.get("DOCGEN_LOCAL_ADAPTER_PATH") or "").strip()

        out = ""
        err: str | None = None
        if not cmd_raw:
            err = "DOCGEN_LLM_SOURCE=local but DOCGEN_LOCAL_LLM_COMMAND is empty."
        else:
            req = {
                "system": system,
                "instruction": prompt,
                "facts_json": json_context,
                "local_model_path": local_model_path,
                "local_adapter_path": local_adapter_path,
            }
            try:
                cmd = shlex.split(cmd_raw, posix=False)
                env = dict(os.environ)
                if local_model_path:
                    env["DOCGEN_LOCAL_MODEL_PATH"] = local_model_path
                if local_adapter_path:
                    env["DOCGEN_LOCAL_ADAPTER_PATH"] = local_adapter_path
                p = subprocess.run(
                    cmd,
                    input=json.dumps(req, ensure_ascii=False).encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=180,
                    env=env,
                )
                if p.returncode != 0:
                    err = f"Local LLM command failed (exit {p.returncode}): {p.stderr.decode('utf-8', 'replace')}"
                else:
                    out = p.stdout.decode("utf-8", "replace").strip()
                    if not out:
                        err = "Local LLM command returned empty output."
            except Exception as e:  # noqa: BLE001
                err = f"Local LLM execution error: {e}"

        if err:
            out = _stub_paragraph(json_context, prompt)
            if log_ctx:
                _write_log_block(
                    log_ctx,
                    seq=seq,
                    mode="local_error_stub",
                    instruction_prompt=prompt,
                    facts_json=json_context,
                    model="local",
                    api_base=f"local_command={cmd_raw}",
                    request_body={"local_request": req if "req" in locals() else None},
                    http_status=None,
                    response_json=None,
                    extracted_text=out,
                    error_message=err,
                )
            return out

        if log_ctx:
            _write_log_block(
                log_ctx,
                seq=seq,
                mode="local_ok",
                instruction_prompt=prompt,
                facts_json=json_context,
                model="local",
                api_base=f"local_command={cmd_raw}",
                request_body={"local_request": req},
                http_status=None,
                response_json=None,
                extracted_text=out,
                error_message=None,
            )
        return out

    # Default: source == "api"
    if not api_key:
        out = _stub_paragraph(json_context, prompt)
        if log_ctx:
            _write_log_block(
                log_ctx,
                seq=seq,
                mode="stub_no_api_key",
                instruction_prompt=prompt,
                facts_json=json_context,
                model=m,
                api_base=root,
                request_body=None,
                http_status=None,
                response_json=None,
                extracted_text=out,
                error_message="No GEMINI_API_KEY / GOOGLE_API_KEY / GENAI_API_KEY set.",
            )
        return out

    url = f"{root}/models/{m}:generateContent"

    try:
        with httpx.Client(timeout=120) as client:
            r = client.post(
                url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            status = r.status_code
            try:
                data = r.json()
            except json.JSONDecodeError:
                data = {"_raw_text": r.text}
            if status >= 400:
                out = _stub_paragraph(json_context, prompt)
                if log_ctx:
                    _write_log_block(
                        log_ctx,
                        seq=seq,
                        mode="gemini_http_error",
                        instruction_prompt=prompt,
                        facts_json=json_context,
                        model=m,
                        api_base=root,
                        request_body=payload,
                        http_status=status,
                        response_json=data if isinstance(data, dict) else None,
                        extracted_text=out,
                        error_message=f"HTTP {status}; using stub paragraph.",
                    )
                print(
                    f"docgen: Gemini request failed (HTTP {status}); using stub paragraph.",
                    file=sys.stderr,
                )
                return out
            out = _gemini_text_from_response(data)
            if log_ctx:
                _write_log_block(
                    log_ctx,
                    seq=seq,
                    mode="gemini_ok",
                    instruction_prompt=prompt,
                    facts_json=json_context,
                    model=m,
                    api_base=root,
                    request_body=payload,
                    http_status=status,
                    response_json=data if isinstance(data, dict) else None,
                    extracted_text=out,
                    error_message=None,
                )
            return out
    except httpx.RequestError as e:
        out = _stub_paragraph(json_context, prompt)
        if log_ctx:
            _write_log_block(
                log_ctx,
                seq=seq,
                mode="gemini_network_error",
                instruction_prompt=prompt,
                facts_json=json_context,
                model=m,
                api_base=root,
                request_body=payload,
                http_status=None,
                response_json=None,
                extracted_text=out,
                error_message=str(e),
            )
        print(f"docgen: Gemini request failed ({e}); using stub paragraph.", file=sys.stderr)
        return out
    except (ValueError, KeyError, IndexError) as e:
        out = _stub_paragraph(json_context, prompt)
        if log_ctx:
            _write_log_block(
                log_ctx,
                seq=seq,
                mode="gemini_parse_error",
                instruction_prompt=prompt,
                facts_json=json_context,
                model=m,
                api_base=root,
                request_body=payload,
                http_status=None,
                response_json=None,
                extracted_text=out,
                error_message=str(e),
            )
        print(f"docgen: Unexpected Gemini response ({e}); using stub paragraph.", file=sys.stderr)
        return out
