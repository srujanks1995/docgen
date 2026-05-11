from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from docgen.docx_builder import build_docx
from docgen.llm import LLMLogContext
from docgen.yaml_config import load_document_config
from docgen.zip_bundle import ZipBundle


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    repo_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_env.is_file():
        load_dotenv(repo_env)

    p = argparse.ArgumentParser(
        description="Generate a Word document from a ZIP bundle (JSON, CSVs, images) and input.yaml.",
    )
    p.add_argument("--zip", required=True, type=Path, help="Path to uploaded ZIP archive.")
    p.add_argument("--yaml", required=True, type=Path, help="Path to structure template (input.yaml).")
    p.add_argument("--out", required=True, type=Path, help="Output .docx path.")
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="Do not call remote LLM (use inline stub paragraph text).",
    )
    p.add_argument(
        "--llm-log",
        type=Path,
        default=None,
        metavar="PATH",
        help="Append Gemini prompt/input/response log (default: llm_out.txt next to --out).",
    )
    p.add_argument(
        "--no-llm-log",
        action="store_true",
        help="Do not write an LLM trace file.",
    )
    args = p.parse_args(argv)

    llm_log_ctx: LLMLogContext | None = None
    if not args.no_llm_log:
        log_path = args.llm_log if args.llm_log is not None else args.out.parent / "llm_out.txt"
        llm_log_ctx = LLMLogContext(log_path)
        llm_log_ctx.reset()
        llm_log_ctx.append(
            f"docgen LLM trace\nOutput document: {args.out.resolve()}\n"
            f"--no-llm: {args.no_llm}\n\n"
        )

    bundle = ZipBundle.from_zip(args.zip)
    cfg = load_document_config(args.yaml)
    build_docx(bundle, cfg, args.out, use_llm=not args.no_llm, llm_log_ctx=llm_log_ctx)
    print(f"Wrote {args.out.resolve()}")
    if llm_log_ctx:
        print(f"Wrote {llm_log_ctx.path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
