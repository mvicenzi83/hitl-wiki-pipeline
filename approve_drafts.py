"""
approve_drafts.py — HITL: revisione interattiva delle bozze generate da llm_bridge.py.

Per ogni bozza in /drafts mostra il contenuto e chiede:
  [a] approva  → copia la pagina in /wiki/ai (sovrascrive se aggiornamento)
  [r] rifiuta  → sposta la bozza in /drafts/rejected/
  [s] salta    → lascia la bozza in /drafts per dopo
  [q] esci     → interrompe la revisione

Usage:
    python approve_drafts.py          # Revisiona tutte le bozze in /drafts
    python approve_drafts.py <bozza>  # Revisiona una singola bozza
"""

import re
import sys
import shutil
from pathlib import Path
from datetime import datetime

from utils import load_config, get_domains, update_wiki_home, update_domains, extract_personal_notes


# Larghezza del separatore visivo nel terminale
_SEP_WIDTH = 70


def extract_page_slug(draft_content: str) -> str | None:
    """
    Estrae il titolo H1 dalla bozza per derivare il nome file wiki.
    Ritorna lo slug (lowercase, spazi -> trattino) o None se non trovato.
    """
    for line in draft_content.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            return title.lower().replace(" ", "-")
    return None


def show_draft(draft_path: Path, draft_content: str) -> None:
    """Stampa la bozza a video, saltando il blocco di metadata HTML."""
    print("=" * _SEP_WIDTH)
    print(f"  BOZZA: {draft_path.name}")
    print("=" * _SEP_WIDTH)

    # Rimuove il commento HTML di metadata per la visualizzazione
    clean = re.sub(r"<!--.*?-->\n*", "", draft_content, flags=re.DOTALL).strip()
    print(clean)
    print("-" * _SEP_WIDTH)


def extract_draft_metadata(draft_content: str) -> dict:
    """Estrae i campi dal commento HTML di metadata."""
    meta = {}
    pattern = r"^(\w[\w '.]+?):\s*(.+)$"
    in_comment = False
    for line in draft_content.splitlines():
        if "<!--" in line:
            in_comment = True
        if "-->" in line:
            in_comment = False
        if in_comment:
            m = re.match(pattern, line.strip())
            if m:
                meta[m.group(1).lower().replace("'", "").replace(" ", "_")] = m.group(2).strip()
    return meta


def process_draft(
    draft_path: Path,
    wiki_path: Path,
    rejected_dir: Path,
    cfg: dict | None = None,
) -> str:
    """
    Mostra una bozza e gestisce l'input utente.
    Ritorna: 'approved' | 'rejected' | 'skipped' | 'quit'
    """
    draft_content = draft_path.read_text(encoding="utf-8")
    show_draft(draft_path, draft_content)

    meta = extract_draft_metadata(draft_content)
    draft_type = meta.get("tipo", "?")
    wiki_match = meta.get("wiki_match", "nessuno")

    if draft_type == "update":
        print(f"  Tipo:    AGGIORNAMENTO di [[{wiki_match}]]")
    else:
        print(f"  Tipo:    NUOVA PAGINA")

    while True:
        choice = input("\n  [a] approva  [r] rifiuta  [s] salta  [q] esci > ").strip().lower()

        if choice == "a":
            # Determina il nome file di destinazione
            clean_body = re.sub(r"<!--.*?-->\n*", "", draft_content, flags=re.DOTALL).strip()
            slug = extract_page_slug(clean_body)
            if not slug:
                print("  [!] Impossibile estrarre il titolo H1 dalla bozza. Operazione annullata.")
                return "skipped"

            dest_path = wiki_path / f"{slug}.md"
            personal_notes = extract_personal_notes(dest_path.read_text(encoding="utf-8")) if dest_path.exists() else ""
            final_body = clean_body.rstrip() + personal_notes if personal_notes else clean_body
            dest_path.write_text(final_body, encoding="utf-8")

            action = "aggiornata" if dest_path.exists() else "creata"
            print(f"  [OK] Pagina {action}: {dest_path.name}")

            if cfg is not None:
                domain_match = re.search(r"^\*\*Domain\*\*:\s*(\S+)", clean_body, re.MULTILINE)
                domain_name  = domain_match.group(1).strip() if domain_match else None
                summary_match = re.search(r"^\*\*Summary\*\*:\s*(.+)$", clean_body, re.MULTILINE)
                summary      = summary_match.group(1).strip() if summary_match else ""
                if domain_name and summary:
                    update_wiki_home(slug, summary, domain_name, cfg)
                    print(f"  [OK] wiki-home.md aggiornato.")

                # Aggiorna domains.md se la pagina ha il campo Domains
                domains_field = re.search(r"^\*\*Domains\*\*:\s*(.+)$", clean_body, re.MULTILINE)
                if domains_field:
                    domain_slugs = re.findall(r"\[\[([^\]]+)\]\]", domains_field.group(1))
                    if domain_slugs:
                        added = update_domains(domain_slugs, cfg)
                        if added:
                            print(f"  [OK] Nuovi domini aggiunti a domains.md: {', '.join(added)}")

            # Sposta la bozza approvata in una sottocartella per tracciabilita'
            approved_dir = draft_path.parent / "approved"
            approved_dir.mkdir(exist_ok=True)
            shutil.move(str(draft_path), str(approved_dir / draft_path.name))
            return "approved"

        elif choice == "r":
            rejected_dir.mkdir(exist_ok=True)
            shutil.move(str(draft_path), str(rejected_dir / draft_path.name))
            print(f"  [--] Bozza spostata in rejected/")
            return "rejected"

        elif choice == "s":
            print(f"  [..] Bozza saltata (rimane in /drafts)")
            return "skipped"

        elif choice == "q":
            print("  Revisione interrotta.")
            return "quit"

        else:
            print("  Inserisci: a, r, s oppure q")


def run_approval(cfg: dict, target_file: Path | None = None) -> None:
    domains      = get_domains(cfg)
    wiki_path    = Path(domains[0]["wiki"]) if domains else Path(cfg["paths"]["wiki_base"]) / "ai"
    llm_cfg      = cfg["llm"]
    drafts_dir   = Path(llm_cfg.get("drafts_dir", ""))
    rejected_dir = drafts_dir / "rejected"

    if target_file:
        drafts = [target_file]
    else:
        drafts = sorted(drafts_dir.glob("draft_*.md"))

    if not drafts:
        print(f"Nessuna bozza trovata in {drafts_dir}")
        return

    print(f"\nTrovate {len(drafts)} bozze da revisionare.\n")

    stats = {"approved": 0, "rejected": 0, "skipped": 0}

    for draft_path in drafts:
        result = process_draft(draft_path, wiki_path, rejected_dir, cfg)
        if result == "quit":
            break
        if result in stats:
            stats[result] += 1

    print("\n" + "=" * _SEP_WIDTH)
    print(f"  Revisione completata.")
    print(f"  Approvate: {stats['approved']}  |  Rifiutate: {stats['rejected']}  |  Saltate: {stats['skipped']}")
    print("=" * _SEP_WIDTH)

    if stats["approved"] > 0:
        print("\nEsegui 'python confidence_manager.py' per aggiornare gli score.")


if __name__ == "__main__":
    cfg = load_config()

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if not target.exists():
            print(f"File non trovato: {target}")
            sys.exit(1)
        run_approval(cfg, target_file=target)
    else:
        run_approval(cfg)
