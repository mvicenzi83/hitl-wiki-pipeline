"""
approve_draft_auto.py — Gate 2: pubblica automaticamente una bozza approvata in wiki/ai.

Triggered dal watcher quando l'utente spunta la sentinel ✅ APPROVA BOZZA in una bozza.
Equivale a approve_drafts.py [a] ma guidato dalla checkbox invece dell'input da terminale.

Idempotente: se <!-- published --> è già presente, esce senza fare nulla.

Usage:
    python approve_draft_auto.py <path/to/draft_*.md>
"""

import re
import sys
import shutil
import subprocess
from pathlib import Path

from utils import load_config, get_domains, update_wiki_home, update_domains
from approve_drafts import extract_page_slug, extract_draft_metadata


def run(draft_path: Path, cfg: dict) -> None:
    content = draft_path.read_text(encoding="utf-8")

    # Guard 1: sentinel non spuntata
    if "- [x] ✅ APPROVA BOZZA" not in content:
        return

    # Guard 2: già pubblicato
    if "<!-- published" in content:
        return

    # Rimuovi il commento metadata HTML per estrarre l'H1
    clean_body = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    slug = extract_page_slug(clean_body)

    if not slug:
        print(f"[!] Impossibile estrarre H1 da {draft_path.name}")
        return

    print(f"[>] Approve Draft Auto: {draft_path.name}")
    print(f"    Slug: {slug}")

    # Rimuovi il blocco sentinel finale (--- + riga italics + riga checkbox)
    clean_body = re.sub(
        r"\n\n---\n\*Bozza generata automaticamente.*?- \[.\] ✅ APPROVA BOZZA[^\n]*\n?",
        "",
        clean_body,
        flags=re.DOTALL,
    ).strip()

    # Risolvi il dominio dal frontmatter del draft (**Domain**: <name>)
    domain_match = re.search(r"^\*\*Domain\*\*:\s*(\S+)", clean_body, re.MULTILINE)
    domain_name  = domain_match.group(1).strip() if domain_match else None
    domains      = get_domains(cfg)
    domain       = next((d for d in domains if d["name"] == domain_name), domains[0] if domains else None)
    if not domain:
        print(f"[!] Nessun dominio trovato in config per: {domain_name}")
        return
    wiki_path = Path(domain["wiki"])
    dest_path = wiki_path / f"{slug}.md"
    dest_path.write_text(clean_body, encoding="utf-8")
    print(f"    [OK] Pubblicata: {dest_path.name}")

    # Aggiorna wiki-home.md
    summary_match = re.search(r"^\*\*Summary\*\*:\s*(.+)$", clean_body, re.MULTILINE)
    summary = summary_match.group(1).strip() if summary_match else ""
    if domain_name and summary:
        update_wiki_home(slug, summary, domain_name, cfg)
        print(f"    [OK] wiki-home.md aggiornato.")

    # Aggiorna domains.md se la pagina ha il campo Domains
    domains_field = re.search(r"^\*\*Domains\*\*:\s*(.+)$", clean_body, re.MULTILINE)
    if domains_field:
        domain_slugs = re.findall(r"\[\[([^\]]+)\]\]", domains_field.group(1))
        if domain_slugs:
            added = update_domains(domain_slugs, cfg)
            if added:
                print(f"    [OK] Nuovi domini aggiunti a domains.md: {', '.join(added)}")

    # Sposta bozza in approved/
    approved_dir = draft_path.parent / "approved"
    approved_dir.mkdir(exist_ok=True)
    shutil.move(str(draft_path), str(approved_dir / draft_path.name))
    print(f"    [OK] Bozza spostata in approved/")

    # Aggiorna confidence scores
    _HERE          = Path(__file__).parent
    manager_script = _HERE / "confidence_manager.py"
    try:
        subprocess.run([sys.executable, str(manager_script)], check=True)
        print(f"    [OK] Health report aggiornato.")
    except subprocess.CalledProcessError as e:
        print(f"    [!] Errore nel Confidence Manager: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python approve_draft_auto.py <path/to/draft_*.md>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"File non trovato: {target}")
        sys.exit(1)

    cfg = load_config()
    run(target, cfg)
