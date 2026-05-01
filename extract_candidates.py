"""
extract_candidates.py — Pre-processing: estrae entità candidate dai file in /raw
e le scrive come JSON in /candidates per revisione LLM successiva.

Usage:
    python extract_candidates.py              # Processa tutti i file in raw_ai
    python extract_candidates.py <file.md>    # Processa solo il file specificato
"""

import sys
import json
from pathlib import Path

from utils import load_config, extract_entities, get_domains, get_domain_for_file


def process_raw_files(raw_path: Path, output_dir: Path, cfg: dict, domain_name: str) -> None:
    files = list(raw_path.glob("**/*.md"))
    if not files:
        print(f"Nessun file .md trovato in {raw_path}")
        return

    for f_path in files:
        text = f_path.read_text(encoding="utf-8")
        candidates = extract_entities(text, cfg)
        json_filename = f"candidates_{f_path.stem}.json"

        with open(output_dir / json_filename, "w", encoding="utf-8") as jf:
            json.dump(
                {
                    "source_file": f_path.name,
                    "domain": domain_name,
                    "candidates": candidates,
                    "text_preview": text[:500],  # Contesto rapido per l'LLM
                },
                jf,
                indent=4,
                ensure_ascii=False,
            )

        print(f"  [OK] Candidati generati per: {f_path.name}  ({len(candidates)} entita')")


if __name__ == "__main__":
    cfg = load_config()
    paths = cfg["paths"]

    output_dir  = Path(paths["candidates_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    # Argomento opzionale: processa solo il file specificato
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if not target.exists():
            print(f"File non trovato: {target}")
            sys.exit(1)
        domain = get_domain_for_file(target, cfg)
        domain_name = domain["name"] if domain else "unknown"
        text = target.read_text(encoding="utf-8")
        candidates = extract_entities(text, cfg)
        json_filename = f"candidates_{target.stem}.json"
        with open(output_dir / json_filename, "w", encoding="utf-8") as jf:
            json.dump(
                {"source_file": target.name, "domain": domain_name, "candidates": candidates, "text_preview": text[:500]},
                jf, indent=4, ensure_ascii=False,
            )
        print(f"  [OK] Candidati generati per: {target.name}  ({len(candidates)} entita')")
    else:
        for domain in get_domains(cfg):
            raw_path    = Path(domain["raw"])
            domain_name = domain["name"]
            print(f"Analisi di tutti i file in: {raw_path} (dominio: {domain_name})")
            process_raw_files(raw_path, output_dir, cfg, domain_name)
