"""
ingest_assistant.py — Genera una proposal Markdown per ogni file in /raw,
elencando le entità rilevate con stato wiki (nuovo / già esistente / duplicato fuzzy).

Usage:
    python ingest_assistant.py                      # Processa tutti i file in raw_ai
    python ingest_assistant.py <percorso/file.md>   # Processa solo il file specificato
"""

import sys
from pathlib import Path

from utils import (
    load_config,
    extract_entities,
    get_wiki_files,
    find_best_match,
    get_domains,
    get_domain_for_file,
)


def create_ingest_proposal(
    source_file: Path,
    text: str,
    wiki_path: Path,
    output_dir: Path,
    cfg: dict,
) -> Path:
    wiki_files = get_wiki_files(wiki_path)
    entities   = extract_entities(text, cfg)

    proposal_path = output_dir / f"proposal_{source_file.stem}.md"

    with open(proposal_path, "w", encoding="utf-8") as f:
        f.write(f"# Ingest Proposta: {source_file.name}\n")
        f.write(f"**Sorgente**: `{source_file}`\n\n")
        f.write("## Entità Rilevate\n\n")
        f.write("- [ ] ✅ AVVIA INGEST — spunta dopo aver selezionato le entità sopra\n\n")

        if not entities:
            f.write("Nessuna entità rilevante rilevata dopo il filtraggio.\n")
        else:
            for item in entities:
                entity = item["entity"]
                etype  = item["type"]
                count  = item["count"]
                match, is_exact = find_best_match(entity, wiki_files)

                if is_exact:
                    status = f"*Già esistente (freq: {count})*"
                elif match:
                    status = f"*Sospetto duplicato di [[{match}]]? (freq: {count})*"
                else:
                    status = f"*Nuova pagina (freq: {count})*"

                f.write(f"- [ ] [[{entity}]] (Tipo: {etype}) → {status}\n")

        f.write("\n## Sintesi Suggerita\n\n")
        preview = "\n".join(text.splitlines()[:3])
        f.write(f"```\n{preview}\n...\n```\n")
        f.write("\n---\n*Proposal generata automaticamente — richiede revisione umana prima dell'ingest.*\n")

    return proposal_path


def process_file(source_file: Path, wiki_path: Path, output_dir: Path, cfg: dict) -> None:
    text = source_file.read_text(encoding="utf-8")
    proposal = create_ingest_proposal(source_file, text, wiki_path, output_dir, cfg)
    print(f"  [OK] Proposal creata: {proposal.name}")


if __name__ == "__main__":
    cfg   = load_config()
    paths = cfg["paths"]

    output_dir   = Path(paths["proposals_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1:
        # Modalità singolo file — passato dal watcher o dalla CLI
        target = Path(sys.argv[1])
        if not target.exists():
            print(f"File non trovato: {target}")
            sys.exit(1)
        domain    = get_domain_for_file(target, cfg)
        domains   = get_domains(cfg)
        wiki_path = Path(domain["wiki"]) if domain else Path(domains[0]["wiki"])
        process_file(target, wiki_path, output_dir, cfg)
    else:
        # Modalità batch — tutti i file per ogni dominio configurato
        for domain in get_domains(cfg):
            raw_path  = Path(domain["raw"])
            wiki_path = Path(domain["wiki"])
            raw_files = list(raw_path.glob("**/*.md"))
            if not raw_files:
                print(f"Nessun file .md trovato in {raw_path}")
                continue
            print(f"Analisi di {len(raw_files)} file in: {raw_path} (dominio: {domain['name']})")
            for f in raw_files:
                process_file(f, wiki_path, output_dir, cfg)
