"""
wiki_maintenance.py — Analisi della wiki in modalità read-only.

Produce un report markdown con problemi strutturali e (opzionalmente) analisi LLM.
Non modifica mai le pagine wiki.

Usage:
    python wiki_maintenance.py                  # check strutturali (no LLM)
    python wiki_maintenance.py --full           # + analisi LLM (accuratezza + link impliciti)
    python wiki_maintenance.py --page alphago   # analisi di una singola pagina (sempre full)
"""

import re
import argparse
from datetime import datetime
from pathlib import Path

from utils import load_config, get_domains
from llm_bridge import call_llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_link(text: str) -> str:
    """Converte un link wiki grezzo allo slug normalizzato: lowercase + strip + spazi/punti→trattini."""
    return text.strip().lower().replace(" ", "-").replace(".", "-")


def parse_wiki_links(text: str) -> list[str]:
    """Estrae tutti i [[link]] dal testo. Ritorna la lista dei target raw (non normalizzati)."""
    return re.findall(r"\[\[([^\]]+)\]\]", text)


def get_related_section_links(content: str) -> list[str]:
    """Estrae i [[link]] presenti solo nella sezione ## Related pages."""
    match = re.search(r"## Related pages(.*?)(?=^##|\Z)", content, re.DOTALL | re.MULTILINE)
    if not match:
        return []
    return parse_wiki_links(match.group(1))


def is_source_page(content: str) -> bool:
    """True se la pagina è una source page (Sources punta a raw/)."""
    return bool(re.search(r"\*\*Sources\*\*:\s*`raw/", content))


def build_citation_map(pages: dict[str, str]) -> dict[str, int]:
    """
    Conta le citazioni inbound per ogni pagina.
    # TODO: pre-hybrid-search — estrarre in utils.py per evitare duplicazione con confidence_manager.py
    """
    citation_map = {slug: 0 for slug in pages}
    for content in pages.values():
        for raw_link in parse_wiki_links(content):
            target = normalize_link(raw_link.split("|")[0])
            if target in citation_map:
                citation_map[target] += 1
    return citation_map


def load_pages(wiki_path: Path, excluded: set[str]) -> dict[str, str]:
    """Carica tutte le pagine .md dal wiki, escluse quelle in excluded. Ritorna {slug: content}."""
    pages = {}
    for f in wiki_path.glob("*.md"):
        if f.stem not in excluded:
            pages[f.stem] = f.read_text(encoding="utf-8")
    return pages


# ---------------------------------------------------------------------------
# Check functions — Python-pure (Fasi 1–5)
# ---------------------------------------------------------------------------

def check_broken_links(pages: dict[str, str]) -> tuple[list, list]:
    """
    Fase 1: Broken links e link mal formattati.

    Returns:
        missing:   list of (page_slug, raw_link)             — link genuinamente mancante  🔴
        malformed: list of (page_slug, raw_link, correct_slug) — slug sbagliato ma pagina esiste 🟡
    """
    existing = set(pages.keys())
    missing = []
    malformed = []

    for slug, content in pages.items():
        seen_missing = set()    # (slug, normalized) — un report per target mancante
        seen_malformed = set()  # (slug, raw_link)   — un report per link malformato
        for raw_link in parse_wiki_links(content):
            target_raw = raw_link.split("|")[0]
            normalized = normalize_link(target_raw)
            if normalized in existing:
                # La pagina target esiste: segnala solo se il link è mal formattato
                if target_raw.strip() != normalized:
                    key = (slug, raw_link)
                    if key not in seen_malformed:
                        seen_malformed.add(key)
                        malformed.append((slug, raw_link, normalized))
            else:
                # La pagina target manca: un solo report per target per pagina
                key = (slug, normalized)
                if key not in seen_missing:
                    seen_missing.add(key)
                    missing.append((slug, raw_link))

    return missing, malformed


def check_orphan_pages(pages: dict[str, str], citation_map: dict[str, int]) -> list[str]:
    """
    Fase 2: Pagine orfane (0 citazioni inbound, non source page).
    """
    return [
        slug for slug, content in pages.items()
        if citation_map.get(slug, 0) == 0 and not is_source_page(content)
    ]


def check_bidirectionality(pages: dict[str, str]) -> list[tuple[str, str]]:
    """
    Fase 3: A cita B in Related pages ma B non cita A in Related pages.
    Returns: list of (page_a, page_b) dove il link inverso manca.
    """
    issues = []
    for slug_a, content_a in pages.items():
        related_a = {
            normalize_link(lnk.split("|")[0])
            for lnk in get_related_section_links(content_a)
        }
        for slug_b in related_a:
            if slug_b not in pages:
                continue  # broken link — già coperto dalla Fase 1
            related_b = {
                normalize_link(lnk.split("|")[0])
                for lnk in get_related_section_links(pages[slug_b])
            }
            if slug_a not in related_b:
                issues.append((slug_a, slug_b))
    return issues


def check_source_integrity(
    pages: dict[str, str], workspace_root: Path
) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Fase 4: Source pages senza **Domains**: e con path raw inesistente.

    Returns:
        no_domains: list of slug senza campo **Domains**:
        bad_paths:  list of (slug, raw_path) dove il file raw non esiste su disco
    """
    no_domains = []
    bad_paths = []

    for slug, content in pages.items():
        if not is_source_page(content):
            continue
        if not re.search(r"\*\*Domains\*\*:", content):
            no_domains.append(slug)
        m = re.search(r"\*\*Sources\*\*:\s*`(raw/[^`]+)`", content)
        if m:
            raw_rel = m.group(1)
            full_path = workspace_root / raw_rel
            if not full_path.exists():
                bad_paths.append((slug, raw_rel))

    return no_domains, bad_paths


def check_concept_source_exists(pages: dict[str, str]) -> list[tuple[str, str]]:
    """
    Fase 5: Concept pages con **Sources**: [[slug]] ma la source page non esiste.
    Returns: list of (concept_slug, missing_source_slug)
    """
    issues = []
    for slug, content in pages.items():
        if is_source_page(content):
            continue
        m = re.search(r"\*\*Sources\*\*:\s*\[\[([^\]]+)\]\]", content)
        if m:
            source_slug = normalize_link(m.group(1))
            if source_slug not in pages:
                issues.append((slug, source_slug))
    return issues


# ---------------------------------------------------------------------------
# LLM check functions — Fasi 6–7 (solo --full)
# ---------------------------------------------------------------------------

PROMPT_ACCURACY = """\
Sei un assistente specializzato nel controllo di accuratezza di pagine wiki.

Hai di fronte una pagina wiki e un estratto della sorgente da cui deriva.
Segnala SOLO affermazioni nella pagina wiki che:
- Contraddicono il testo della sorgente
- Non sono supportate da nessuna parte della sorgente (non si tratta di omissioni \
accettabili, ma di affermazioni false o distorte)

--- PAGINA WIKI: [[{slug}]] ---
{wiki_content}
--- FINE PAGINA WIKI ---

--- ESTRATTO SORGENTE: {source_file} ---
{source_excerpt}
--- FINE ESTRATTO ---

Rispondi SOLO con:
- Un elenco puntato delle discrepanze trovate (massimo 5), ciascuna con la citazione \
esatta dalla wiki e la contraddizione con la sorgente.
- Se non trovi discrepanze, scrivi esattamente: "Nessuna discrepanza rilevata."

Non aggiungere introduzioni o conclusioni.
"""

PROMPT_IMPLICIT_LINKS = """\
Sei un assistente specializzato nell'analisi di pagine wiki.

Nel testo della pagina qui sotto, individua i concetti che:
1. Vengono citati nel corpo del testo (NON nelle sezioni Sources o Related pages)
2. NON sono già wrappati in [[wiki-link]]
3. MA corrispondono a una delle pagine wiki esistenti nella lista fornita

--- PAGINA WIKI: [[{slug}]] ---
{wiki_content}
--- FINE PAGINA WIKI ---

--- PAGINE WIKI ESISTENTI ---
{existing_pages}
--- FINE LISTA ---

Rispondi SOLO con un elenco puntato nel formato:
- "testo citato nella pagina" → [[slug-corrispondente]]

Se non trovi corrispondenze, scrivi esattamente: "Nessun link implicito rilevato."
Non aggiungere introduzioni o conclusioni.
"""


def check_accuracy(
    pages: dict[str, str], workspace_root: Path, cfg: dict
) -> list[tuple[str, str]]:
    """
    Fase 6: Confronta ogni concept page con il raw della sorgente collegata.
    Returns: list of (slug, discrepancy_text)
    """
    excerpt_chars = cfg.get("maintenance", {}).get("accuracy_excerpt_chars", 4000)
    results = []

    for slug, content in pages.items():
        if is_source_page(content):
            continue
        m = re.search(r"\*\*Sources\*\*:\s*\[\[([^\]]+)\]\]", content)
        if not m:
            continue
        source_slug = normalize_link(m.group(1))
        if source_slug not in pages:
            continue  # già segnalato dalla Fase 5
        source_content = pages[source_slug]
        raw_m = re.search(r"\*\*Sources\*\*:\s*`(raw/[^`]+)`", source_content)
        if not raw_m:
            continue
        raw_file = workspace_root / raw_m.group(1)
        if not raw_file.exists():
            continue  # già segnalato dalla Fase 4

        source_text = raw_file.read_text(encoding="utf-8")[:excerpt_chars]
        prompt = PROMPT_ACCURACY.format(
            slug=slug,
            wiki_content=content,
            source_file=raw_m.group(1),
            source_excerpt=source_text,
        )
        print(f"  [LLM] Fase 6 — accuracy: [[{slug}]]")
        response = call_llm(prompt, cfg)
        if response and "Nessuna discrepanza rilevata" not in response:
            results.append((slug, response.strip()))

    return results


def check_implicit_links(
    pages: dict[str, str], cfg: dict
) -> list[tuple[str, str]]:
    """
    Fase 7: Individua concetti citati senza [[link]] ma con pagina wiki esistente.
    Returns: list of (slug, suggestions_text)
    """
    existing_list = "\n".join(f"- {s}" for s in sorted(pages.keys()))
    results = []

    for slug, content in pages.items():
        prompt = PROMPT_IMPLICIT_LINKS.format(
            slug=slug,
            wiki_content=content,
            existing_pages=existing_list,
        )
        print(f"  [LLM] Fase 7 — implicit links: [[{slug}]]")
        response = call_llm(prompt, cfg)
        if response and "Nessun link implicito rilevato" not in response:
            results.append((slug, response.strip()))

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    mode: str,
    missing_links: list,
    malformed_links: list,
    orphans: list,
    bidir_issues: list,
    source_no_domains: list,
    source_bad_paths: list,
    concept_no_source: list,
    accuracy_issues: list,
    implicit_links: list,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Wiki Maintenance Report",
        f"**Generato**: {now}",
        f"**Modalità**: {mode}",
        "",
        "---",
        "",
    ]

    # ---- 🔴 Priorità Alta ----
    high = []

    if missing_links:
        high.append("### Broken Links\n")
        for page, link in missing_links:
            high.append(f"- [[{page}]]: cita `[[{link}]]` → pagina mancante")
        high.append("")

    if concept_no_source:
        high.append("### Concept Pages senza Source Page\n")
        for slug, src in concept_no_source:
            high.append(f"- [[{slug}]]: Sources [[{src}]] → `{src}.md` non esiste in wiki")
        high.append("")

    if source_bad_paths:
        high.append("### Source Pages con Path Raw Inesistente\n")
        for slug, path in source_bad_paths:
            high.append(f"- [[{slug}]]: `{path}` non trovato su disco")
        high.append("")

    lines.append("## 🔴 Priorità Alta")
    lines.append("")
    if high:
        lines.extend(high)
    else:
        lines.append("Nessun problema critico rilevato.")
        lines.append("")

    # ---- 🟡 Attenzione ----
    medium = []

    if malformed_links:
        medium.append("### Link Mal Formattati (slug non normalizzato)\n")
        for page, link, correct in malformed_links:
            medium.append(f"- [[{page}]]: `[[{link}]]` → suggerito `[[{correct}]]`")
        medium.append("")

    if orphans:
        medium.append("### Pagine Orfane (0 citazioni inbound)\n")
        for slug in orphans:
            medium.append(f"- [[{slug}]]")
        medium.append("")

    if bidir_issues:
        medium.append("### Bidirezionalità Incompleta\n")
        for a, b in bidir_issues:
            medium.append(
                f"- [[{a}]] linka [[{b}]] in Related pages, ma [[{b}]] non linka [[{a}]]"
            )
        medium.append("")

    if source_no_domains:
        medium.append("### Source Pages senza campo Domains\n")
        for slug in source_no_domains:
            medium.append(f"- [[{slug}]]")
        medium.append("")

    lines.append("## 🟡 Attenzione")
    lines.append("")
    if medium:
        lines.extend(medium)
    else:
        lines.append("Nessun problema segnalato.")
        lines.append("")

    # ---- 🟢 Info ----
    lines.append("## 🟢 Info")
    lines.append("")
    lines.append(
        "*(Sezione riservata a metriche informative — da espandere in versioni future)*"
    )
    lines.append("")

    # ---- 🤖 LLM ----
    if mode == "full":
        lines.append("## 🤖 Analisi LLM")
        lines.append("")

        lines.append("### Accuratezza")
        lines.append("")
        if accuracy_issues:
            for slug, text in accuracy_issues:
                lines.append(f"#### [[{slug}]]")
                lines.append("")
                lines.append(text)
                lines.append("")
        else:
            lines.append("Nessuna discrepanza rilevata.")
            lines.append("")

        lines.append("### Link Impliciti Suggeriti")
        lines.append("")
        if implicit_links:
            for slug, text in implicit_links:
                lines.append(f"#### [[{slug}]]")
                lines.append("")
                lines.append(text)
                lines.append("")
        else:
            lines.append("Nessun link implicito rilevato.")
            lines.append("")

    # ---- Azioni Suggerite ----
    lines.append("## Azioni Suggerite")
    lines.append("")

    actions = []
    for page, link in missing_links:
        slug_suggestion = normalize_link(link.split("|")[0])
        actions.append(f"- [ ] Crea stub page per `[[{slug_suggestion}]]` (citata in [[{page}]])")
    for slug, src in concept_no_source:
        actions.append(f"- [ ] Crea o linka source page per [[{src}]] (richiesta da [[{slug}]])")
    for slug, path in source_bad_paths:
        actions.append(f"- [ ] Verifica path raw `{path}` in [[{slug}]]")
    for page, link, correct in malformed_links:
        actions.append(f"- [ ] In [[{page}]], rinomina `[[{link}]]` → `[[{correct}]]`")
    for slug in source_no_domains:
        actions.append(f"- [ ] Aggiungi campo `**Domains**:` a [[{slug}]]")
    for a, b in bidir_issues:
        actions.append(f"- [ ] In [[{b}]], aggiungi [[{a}]] in Related pages")

    if actions:
        lines.extend(actions)
    else:
        lines.append("Nessuna azione richiesta.")

    lines.append("")
    lines.append("---")
    lines.append(
        "*Report generato automaticamente da wiki_maintenance.py — non modificare manualmente.*"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wiki Maintenance — analisi strutturale e LLM."
    )
    parser.add_argument(
        "--full", action="store_true", help="Abilita analisi LLM (Fase 6 + 7)"
    )
    parser.add_argument(
        "--page",
        type=str,
        default=None,
        metavar="SLUG",
        help="Analizza una singola pagina (slug senza .md); implica --full",
    )
    args = parser.parse_args()

    cfg = load_config()
    maint_cfg = cfg.get("maintenance", {})
    excluded = set(
        maint_cfg.get("excluded_pages", ["health-report", "maintenance-report", "domains"])
    )
    report_path = Path(
        maint_cfg.get("report_path", "D:/obsidian_git/wiki/ai/maintenance-report.md")
    )

    domains = get_domains(cfg)
    ai_domain = next((d for d in domains if d["name"] == "ai"), None)
    if not ai_domain:
        print("[!] Dominio 'ai' non trovato in config.toml.")
        return

    wiki_path = Path(ai_domain["wiki"])
    workspace_root = Path(cfg["paths"]["raw_base"]).parent  # D:/obsidian_git

    pages = load_pages(wiki_path, excluded)

    # --page implica --full e filtra i check alla pagina richiesta
    if args.page:
        if args.page not in pages:
            available = ", ".join(sorted(pages.keys()))
            print(f"[!] Pagina '{args.page}' non trovata. Disponibili: {available}")
            return
        args.full = True
        check_pages = {args.page: pages[args.page]}
    else:
        check_pages = pages

    mode = "full" if args.full else "light"
    print(f"[*] Wiki Maintenance — modalità: {mode}")
    print(f"[*] Pagine caricate: {len(pages)} (in check: {len(check_pages)})")

    # Citation map sempre su tutte le pagine per un conteggio accurato
    citation_map = build_citation_map(pages)

    print("[*] Fase 1  — Broken links...")
    missing_links, malformed_links = check_broken_links(check_pages)

    print("[*] Fase 2  — Pagine orfane...")
    orphans = check_orphan_pages(check_pages, citation_map) if not args.page else []

    print("[*] Fase 3  — Bidirezionalità...")
    bidir_issues = check_bidirectionality(check_pages)

    print("[*] Fase 4  — Integrità source pages...")
    source_no_domains, source_bad_paths = check_source_integrity(check_pages, workspace_root)

    print("[*] Fase 5  — Concept pages senza source page...")
    concept_no_source = check_concept_source_exists(check_pages)

    accuracy_issues: list = []
    implicit_links: list = []

    if args.full:
        print("[*] Fase 6  — Accuratezza (LLM)...")
        accuracy_issues = check_accuracy(check_pages, workspace_root, cfg)

        print("[*] Fase 7  — Link impliciti (LLM)...")
        implicit_links = check_implicit_links(check_pages, cfg)

    print("[*] Generazione report...")
    report = generate_report(
        mode=mode,
        missing_links=missing_links,
        malformed_links=malformed_links,
        orphans=orphans,
        bidir_issues=bidir_issues,
        source_no_domains=source_no_domains,
        source_bad_paths=source_bad_paths,
        concept_no_source=concept_no_source,
        accuracy_issues=accuracy_issues,
        implicit_links=implicit_links,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"[✓] Report scritto in: {report_path}")


if __name__ == "__main__":
    main()
