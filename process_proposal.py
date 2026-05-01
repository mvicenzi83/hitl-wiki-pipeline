"""
process_proposal.py — Gate 1: processa le entità approvate nella proposal.

Legge la proposal, controlla la sentinel AVVIA INGEST, estrae le entità spuntate
e genera bozze LLM per ognuna tramite generate_draft_for_entity().

Idempotente: se <!-- processed --> è già presente, esce senza fare nulla.

Usage:
    python process_proposal.py <path/to/proposal_*.md>
"""

import re
import sys
import json
from datetime import datetime
from pathlib import Path

from utils import load_config, get_wiki_files, get_domains
from llm_bridge import generate_draft_for_entity, generate_source_page


def run(proposal_path: Path, cfg: dict) -> None:
    content = proposal_path.read_text(encoding="utf-8")

    # Guard 1: sentinel non spuntata
    if "- [x] ✅ AVVIA INGEST" not in content:
        return

    # Guard 2: già processato
    if "<!-- processed" in content:
        return

    # Estrai entità approvate (righe [x] [[Nome]] che non sono la sentinel)
    entities = re.findall(
        r"^- \[x\] \[\[(.+?)\]\]",
        content,
        re.MULTILINE,
    )
    # Filtra la riga sentinel nel caso in cui sia stata catturata
    entities = [e for e in entities if "AVVIA INGEST" not in e]

    if not entities:
        print(f"  Nessuna entità spuntata in {proposal_path.name}.")
        return

    print(f"[>] Process Proposal: {proposal_path.name}")
    print(f"    Entità selezionate: {', '.join(entities)}")

    # Deriva il nome del candidates JSON dal nome della proposal
    # "proposal_Agentmemory.md" -> "candidates_Agentmemory.json"
    stem        = proposal_path.stem           # "proposal_Agentmemory"
    source_name = stem[len("proposal_"):]      # "Agentmemory"
    candidates_path = (
        Path(cfg["paths"]["candidates_dir"]) / f"candidates_{source_name}.json"
    )

    if not candidates_path.exists():
        print(f"  [!] Candidates JSON non trovato: {candidates_path.name}")
        candidates_by_entity = {}
        source_file   = source_name + ".md"
        source_excerpt = ""
        domain_name = None
    else:
        data = json.loads(candidates_path.read_text(encoding="utf-8"))
        source_file         = data["source_file"]
        domain_name         = data.get("domain")
        candidates_by_entity = {c["entity"]: c for c in data["candidates"]}

        domains   = get_domains(cfg)
        domain    = next((d for d in domains if d["name"] == domain_name), domains[0] if domains else None)
        raw_path  = Path(domain["raw"])  if domain else Path("")
        source_full = raw_path / source_file
        if not source_full.exists():
            found = list(raw_path.glob(f"**/{source_file}")) if raw_path != Path("") else []
            source_full = found[0] if found else source_full
        raw_text    = source_full.read_text(encoding="utf-8") if source_full.exists() else ""
        excerpt_chars  = cfg["llm"].get("source_excerpt_chars", 3000)
        source_excerpt = raw_text[:excerpt_chars]

    # Risolve wiki_path dal domain (o primo dominio come fallback)
    domains   = get_domains(cfg)
    domain    = next((d for d in domains if d["name"] == domain_name), domains[0] if domains else None)
    today_str  = datetime.now().strftime("%Y-%m-%d")
    wiki_path  = Path(domain["wiki"]) if domain else Path("")
    drafts_dir = Path(cfg["llm"]["drafts_dir"])
    drafts_dir.mkdir(parents=True, exist_ok=True)

    existing_wiki = get_wiki_files(wiki_path)

    # Genera prima la source page (se non esiste ancora)
    child_slugs = [e.lower().replace(" ", "-") for e in entities]
    try:
        generate_source_page(
            source_file=source_file,
            source_text=raw_text,
            child_slugs=child_slugs,
            today_str=today_str,
            cfg=cfg,
            wiki_path=wiki_path,
            drafts_dir=drafts_dir,
            domain_name=domain_name or (domain["name"] if domain else "ai"),
        )
    except Exception as e:
        print(f"  [!] Errore source page: {e}")

    for entity in entities:
        candidate = candidates_by_entity.get(entity)
        if candidate:
            etype = candidate.get("type", "Concept/Term")
        else:
            # Entità aggiunta manualmente non presente nei candidates
            etype = "Concept/Term"

        try:
            generate_draft_for_entity(
                entity=entity,
                etype=etype,
                source_file=source_file,
                source_excerpt=source_excerpt,
                today_str=today_str,
                cfg=cfg,
                existing_wiki=existing_wiki,
                wiki_path=wiki_path,
                drafts_dir=drafts_dir,
            )
        except Exception as e:
            print(f"  [!] Errore per '{entity}': {e}")

    # Marca come processato (append — non altera le checkbox)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(proposal_path, "a", encoding="utf-8") as f:
        f.write(f"\n<!-- processed: {timestamp} -->")

    print(f"  [OK] Proposal marcata come processata.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python process_proposal.py <path/to/proposal_*.md>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"File non trovato: {target}")
        sys.exit(1)

    cfg = load_config()
    run(target, cfg)
