"""
apply_maintenance.py — Applica le azioni spuntate [x] nel maintenance-report.

Legge il report e applica SOLO le azioni marcate [x]:
  - "rinomina [[old]] → [[new]]" in una pagina
  - "aggiungi [[slug]] in Related pages" di una pagina

Non tocca azioni non spuntate ([ ]).
Non crea mai nuove pagine (le stub richiedono llm_bridge).

Usage:
    python apply_maintenance.py           # dry-run: mostra le modifiche senza applicarle
    python apply_maintenance.py --apply   # applica effettivamente le modifiche
"""

import re
import argparse
from pathlib import Path

from utils import load_config, get_domains

# ---------------------------------------------------------------------------
# Regex per parsing delle azioni nel report
# ---------------------------------------------------------------------------

RE_RENAME = re.compile(
    r"- \[x\] In \[\[([^\]]+)\]\], rinomina `\[\[([^\]]+)\]\]` → `\[\[([^\]]+)\]\]`"
)
RE_ADD_RELATED = re.compile(
    r"- \[x\] In \[\[([^\]]+)\]\], aggiungi \[\[([^\]]+)\]\] in Related pages"
)


# ---------------------------------------------------------------------------
# Parsing del report
# ---------------------------------------------------------------------------

def parse_checked_actions(report_path: Path) -> tuple[list, list]:
    """
    Legge il report e ritorna le sole azioni spuntate [x].

    Returns:
        renames:      list of (page_slug, old_link_text, new_slug)
        add_related:  list of (page_slug, slug_to_add)
    """
    text = report_path.read_text(encoding="utf-8")
    renames = [
        (m.group(1), m.group(2), m.group(3))
        for m in RE_RENAME.finditer(text)
    ]
    add_related = [
        (m.group(1), m.group(2))
        for m in RE_ADD_RELATED.finditer(text)
    ]
    return renames, add_related


# ---------------------------------------------------------------------------
# Operazioni sui contenuti
# ---------------------------------------------------------------------------

def apply_rename(content: str, old_link: str, new_slug: str) -> tuple[str, int]:
    """
    Sostituisce [[old_link]] con [[new_slug]] ovunque nel testo.
    Returns: (new_content, numero_sostituzioni)
    """
    pattern = re.compile(re.escape(f"[[{old_link}]]"))
    new_content, count = pattern.subn(f"[[{new_slug}]]", content)
    return new_content, count


def apply_add_related(content: str, slug_to_add: str) -> tuple[str, bool]:
    """
    Aggiunge '- [[slug_to_add]]' in fondo alla sezione ## Related pages.
    Returns: (new_content, was_modified)
    """
    # Fermati al prossimo heading ##, non alla fine del file (potrebbero esserci altre sezioni)
    match = re.search(r"(## Related pages)(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if not match:
        return content, False

    section_body = match.group(2)

    # Non aggiungere se [[slug]] già presente nella sezione
    if f"[[{slug_to_add}]]" in section_body:
        return content, False

    new_link = f"\n- [[{slug_to_add}]]"
    new_section_body = section_body.rstrip("\n") + new_link + "\n"
    new_content = content[: match.start(2)] + new_section_body + content[match.end(2) :]
    return new_content, True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Applica le azioni [x] del maintenance-report alle pagine wiki."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Applica le modifiche su disco (default: dry-run, solo preview)",
    )
    args = parser.parse_args()

    cfg = load_config()
    maint_cfg = cfg.get("maintenance", {})
    report_path = Path(
        maint_cfg.get("report_path", "D:/obsidian_git/wiki/ai/maintenance-report.md")
    )

    domains = get_domains(cfg)
    ai_domain = next((d for d in domains if d["name"] == "ai"), None)
    if not ai_domain:
        print("[!] Dominio 'ai' non trovato in config.toml.")
        return
    wiki_path = Path(ai_domain["wiki"])

    if not report_path.exists():
        print(f"[!] Report non trovato: {report_path}")
        return

    renames, add_related_actions = parse_checked_actions(report_path)

    if not renames and not add_related_actions:
        print("[*] Nessuna azione spuntata [x] trovata nel report.")
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[*] apply_maintenance — modalità: {mode}")
    print(f"[*] {len(renames)} rinomina link, {len(add_related_actions)} aggiunte Related pages")
    print()

    # Accumula modifiche in memoria — scrittura solo alla fine (con --apply)
    changes: dict[str, str] = {}

    def get_content(slug: str) -> str | None:
        """Ritorna il contenuto aggiornato in memoria, o lo legge da disco."""
        if slug in changes:
            return changes[slug]
        page_file = wiki_path / f"{slug}.md"
        if not page_file.exists():
            return None
        return page_file.read_text(encoding="utf-8")

    # ---- Rinomina link ----
    print("── Rinomina link ──")
    for page_slug, old_link, new_slug in renames:
        content = get_content(page_slug)
        if content is None:
            print(f"  [!] {page_slug}.md non trovato — skip")
            continue
        new_content, count = apply_rename(content, old_link, new_slug)
        if count == 0:
            print(
                f"  [~] [[{page_slug}]]: `[[{old_link}]]` non trovato "
                f"— potrebbe essere già corretto"
            )
        else:
            print(
                f"  [✓] [[{page_slug}]]: `[[{old_link}]]` → `[[{new_slug}]]`"
                f" ({count} occorrenza/e)"
            )
            changes[page_slug] = new_content

    print()

    # ---- Aggiungi Related pages ----
    print("── Aggiungi Related pages ──")
    for page_slug, slug_to_add in add_related_actions:
        content = get_content(page_slug)
        if content is None:
            print(f"  [!] {page_slug}.md non trovato — skip")
            continue
        new_content, modified = apply_add_related(content, slug_to_add)
        if not modified:
            print(
                f"  [~] [[{page_slug}]]: [[{slug_to_add}]] già presente "
                f"in Related pages — skip"
            )
        else:
            print(f"  [✓] [[{page_slug}]]: aggiungo [[{slug_to_add}]] in Related pages")
            changes[page_slug] = new_content

    print()

    # ---- Scrittura su disco ----
    if not changes:
        print("[*] Nessuna modifica da applicare.")
        return

    if not args.apply:
        print(f"[*] Dry-run: {len(changes)} pagina/e verrebbero modificate.")
        print("[*] Riesegui con --apply per applicare le modifiche.")
    else:
        for slug, content in changes.items():
            page_file = wiki_path / f"{slug}.md"
            page_file.write_text(content, encoding="utf-8")
            print(f"  [✓] Scritto: {page_file.name}")
        print(f"\n[✓] {len(changes)} pagina/e modificate.")


if __name__ == "__main__":
    main()
