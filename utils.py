"""
utils.py — Libreria condivisa per wiki-v2-implementation.

Contiene:
- load_config()               : lettura config.toml
- get_domains()               : lista dei domini configurati in [[domains]]
- get_domain_for_file()       : risolve il domain dict per un file dato il suo path
- is_technical_noise()        : filtro entità rumore
- extract_entities()          : estrazione entità da testo con classificazione per tipo
- calculate_confidence()      : formula confidence con baseline per pagine nuove
- get_wiki_files()         : lista dei file wiki esistenti
- find_best_match()        : deduplicazione fuzzy vs pagine esistenti
- clean_and_update_properties(): frontmatter YAML parser robusto
"""

import re
import math
import difflib
import tomllib
from pathlib import Path
from datetime import datetime
from collections import Counter

# -----------------------------------------------------------------------
# Percorso canonico del config: sempre relativo a questo file
# -----------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent / "config.toml"


def load_config() -> dict:
    """Carica config.toml e ritorna il dizionario."""
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def get_domains(cfg: dict) -> list[dict]:
    """Ritorna la lista dei domini configurati in [[domains]]."""
    return cfg.get("domains", [])


def get_domain_for_file(file_path: Path, cfg: dict) -> dict | None:
    """Ritorna il domain dict corrispondente al file dato il suo path, o None se non trovato."""
    resolved = Path(file_path).resolve()
    for domain in get_domains(cfg):
        try:
            resolved.relative_to(Path(domain["raw"]).resolve())
            return domain
        except ValueError:
            continue
    return None


# -----------------------------------------------------------------------
# Filtri rumore — unica implementazione canonica
# -----------------------------------------------------------------------

STOPWORDS: set[str] = {
    "well-documented", "human-readable", "step-by-step", "real-world",
    "well-organized", "well-suited", "well-defined", "high-stakes",
    "long-form", "no-code", "one-line", "pre-built", "non-trivial",
    "open-ended", "higher-quality", "knowledge-driven", "well-scoped",
}

# Prefissi che producono aggettivi generici, non concetti/tool
INVALID_PREFIXES: list[str] = ["well-", "non-", "multi-", "high-", "re-"]

# Pattern specifici per variabili CSS/API/WordPress — NON catch-all sui trattini
TECHNICAL_NOISE_PATTERNS: list[str] = [
    r"^stat-.*",
    r"^section-.*",
    r"^per-.*",
    r"^auto-.*",
    r".*-scheme$",
    r".*-attr$",
    r".*-level$",
    r".*-port$",
    r".*-viewer$",
    r".*-logo-.*",
    r".*-symbol$",
    r"^wp-.*",
    r".*-color-.*",
    # Solo termini con 4+ parti separate da trattino: indice sicuro di parametri
    r"^[a-z]+-[a-z]+-[a-z]+-[a-z]+.*$",
]


def is_technical_noise(entity: str) -> bool:
    """Ritorna True se l'entità è rumore tecnico (variabili, CSS, parametri API)."""
    e = entity.lower()
    if e in STOPWORDS:
        return True
    if any(e.startswith(p) for p in INVALID_PREFIXES):
        return True
    if any(re.match(pattern, e) for pattern in TECHNICAL_NOISE_PATTERNS):
        return True
    return False


# -----------------------------------------------------------------------
# Estrazione entità — unica implementazione canonica
# -----------------------------------------------------------------------

def extract_entities(text: str, cfg: dict | None = None) -> list[dict]:
    """
    Estrae entità dal testo con classificazione per tipo basata sull'origine del match.

    Tipi restituiti:
      - "Software/Tool"  : CamelCase (nomi propri, librerie, framework)
      - "Named/Entity"   : match contestuale ("using X", "based on X")
      - "Concept/Term"   : kebab-case (frasi composte, concetti)

    Ritorna lista di dict: {"entity": str, "type": str, "count": int}
    Ordinata per count decrescente.
    """
    if cfg is None:
        cfg = load_config()

    ext = cfg.get("extraction", {})
    min_concept = ext.get("min_count_concept", 2)
    min_tool = ext.get("min_count_tool", 1)

    # Accumulo separato per tipo di origine
    camel_raw: list[str] = []
    kebab_raw: list[str] = []
    named_raw: list[str] = []

    # 1. CamelCase → Software/Tool
    camel_pattern = r"\b(?![A-Z\s]+$)([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b"
    camel_raw.extend(re.findall(camel_pattern, text))

    # 2. kebab-case → Concept/Term
    kebab_pattern = r"\b([a-z]+(?:-[a-z]+)+)\b"
    kebab_raw.extend(re.findall(kebab_pattern, text))

    # 3. Match contestuale → Named/Entity
    context_patterns = [
        r"(?:utilizzando|using|basato su|based on|sviluppato da|developed by)\s+([A-Z][\w\s-]{1,30}?)(?:\.|\,|$|\n)",
        r"([A-Z][\w\s-]{1,30}?)\s+(?:is a library|è una libreria|is a project|è un progetto)",
    ]
    for pattern in context_patterns:
        for m in re.findall(pattern, text, re.IGNORECASE):
            entity = (m[0].strip() if isinstance(m, tuple) else m.strip())
            if len(entity.split()) <= 3:
                named_raw.append(entity)

    # Merge con tipo associato
    type_map: dict[str, str] = {}
    for e in camel_raw:
        type_map.setdefault(e, "Software/Tool")
    for e in named_raw:
        type_map.setdefault(e, "Named/Entity")
    for e in kebab_raw:
        type_map.setdefault(e, "Concept/Term")

    all_raw = camel_raw + named_raw + kebab_raw
    counts = Counter(all_raw)

    results: list[dict] = []
    for entity, count in counts.items():
        if len(entity) < 3:
            continue
        if is_technical_noise(entity):
            continue

        etype = type_map.get(entity, "Concept/Term")

        # Soglie per tipo
        if etype in ("Software/Tool", "Named/Entity") and count < min_tool:
            continue
        if etype == "Concept/Term" and count < min_concept:
            continue

        results.append({"entity": entity, "type": etype, "count": count})

    return sorted(results, key=lambda x: x["count"], reverse=True)


# -----------------------------------------------------------------------
# Confidence Score — unica implementazione canonica
# -----------------------------------------------------------------------

def calculate_confidence(
    citations_count: int,
    last_updated_date: str,
    word_count: int = 0,
    cfg: dict | None = None,
) -> float:
    """
    Formula:
        C = (citations + content_score) / (1 + log(1 + delta_t))

    Dove content_score = base_score + min(word_bonus_max, word_count / word_target)

    - base_score garantisce che pagine nuove non partano da 0.0
    - word_bonus premia pagine con contenuto sviluppato
    - Il denominatore penalizza pagine non aggiornate
    """
    if cfg is None:
        cfg = load_config()

    conf_cfg = cfg.get("confidence", {})
    base_score    = conf_cfg.get("base_score", 1.0)
    word_bonus_max = conf_cfg.get("word_bonus_max", 1.0)
    word_target   = conf_cfg.get("word_target", 300)

    try:
        updated = datetime.strptime(last_updated_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        updated = datetime.now()

    delta_t = max(0, (datetime.now() - updated).days)
    denominator = 1 + math.log1p(delta_t)

    content_score = base_score + min(word_bonus_max, word_count / max(1, word_target))
    confidence = (citations_count + content_score) / denominator

    return round(confidence, 3)


# -----------------------------------------------------------------------
# Utilità wiki — estratte da ingest_assistant.py
# -----------------------------------------------------------------------

def get_wiki_files(wiki_path: Path | str) -> list[str]:
    """Ritorna la lista degli stem dei file .md nella wiki (senza estensione)."""
    return [f.stem for f in Path(wiki_path).glob("**/*.md")]


def find_best_match(entity: str, existing_files: list[str]) -> tuple[str | None, bool]:
    """
    Cerca entity nella lista di file wiki esistenti.
    Ritorna (match, is_exact):
      - is_exact=True  → pagina già esistente con nome identico
      - is_exact=False → sospetto duplicato fuzzy
      - match=None     → nessun match
    """
    for f in existing_files:
        if entity.lower() == f.lower():
            return f, True
    matches = difflib.get_close_matches(entity, existing_files, n=1, cutoff=0.85)
    return (matches[0], False) if matches else (None, False)


# -----------------------------------------------------------------------
# Note personali — estrazione per preservare le annotazioni dell'utente
# -----------------------------------------------------------------------

def extract_personal_notes(content: str) -> str:
    """
    Estrae la sezione '## Note personali' e tutto ciò che segue dal contenuto di una pagina wiki.
    Ritorna la stringa estratta (inclusa la riga H2) se presente, altrimenti stringa vuota.
    Usata dai comandi di approve per preservare le note durante gli aggiornamenti di pagina.
    """
    match = re.search(r"\n## Note personali\n.*", content, re.DOTALL)
    return match.group(0) if match else ""


# -----------------------------------------------------------------------
# Frontmatter YAML — parser robusto (da confidence_analyzer.py)
# -----------------------------------------------------------------------

def clean_and_update_properties(content: str, new_properties: dict) -> str:
    """
    Rimuove il frontmatter YAML esistente (se presente) e lo riscrive da zero
    con le nuove proprietà. Previene duplicati e artefatti di scrittura.
    """
    # Rimuove eventuali '\\n' letterali che possono entrare nei file
    content = content.replace("\\n", "\n")

    # Rimuove il frontmatter esistente (--- ... ---)
    frontmatter_pattern = re.compile(r"^---\s*.*?\s*---", re.DOTALL)
    match = frontmatter_pattern.match(content)
    body = content[match.end():].lstrip() if match else content.lstrip()

    # Ricostruisce frontmatter pulito
    lines = ["---"]
    for k, v in new_properties.items():
        lines.append(f"{k}: {v}")
    lines.append("---")

    return "\n".join(lines) + "\n\n" + body


# -----------------------------------------------------------------------
# Wiki Home — aggiornamento automatico
# -----------------------------------------------------------------------

# Mapping nome dominio → titolo sezione in wiki-home.md
_DOMAIN_SECTION_HEADERS: dict[str, str] = {
    "ai":    "## AI",
    "lytro": "## Lytro / Light Field",
    "ideas": "## Ideas",
}


def update_wiki_home(slug: str, summary: str, domain_name: str, cfg: dict) -> None:
    """
    Aggiunge o aggiorna l'entry [[slug]] nella sezione corretta di wiki-home.md.
    Se la sezione contiene solo '*(no pages yet)*', la sostituisce.
    Le entry vengono mantenute in ordine alfabetico.
    """
    home_path = Path(cfg["paths"]["wiki_base"]) / "wiki-home.md"
    if not home_path.exists():
        return

    section_header = _DOMAIN_SECTION_HEADERS.get(domain_name)
    if not section_header:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    content   = home_path.read_text(encoding="utf-8")
    entry     = f"- [[{slug}]] — {summary}"
    lines     = content.splitlines()

    # Trova l'indice della sezione
    section_start = next(
        (i for i, l in enumerate(lines) if l.strip() == section_header),
        None,
    )
    if section_start is None:
        return

    section_end = next(
        (i for i in range(section_start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )

    # Estrae il corpo della sezione (senza l'header)
    body = lines[section_start + 1 : section_end]

    # Rimuove placeholder "*(no pages yet)*"
    body = [l for l in body if "*(no pages yet)*" not in l]

    # Aggiorna entry esistente o aggiunge nuova
    existing_idx = next((i for i, l in enumerate(body) if f"[[{slug}]]" in l), None)
    if existing_idx is not None:
        body[existing_idx] = entry
    else:
        body.append(entry)

    # Ordina alfabeticamente le righe entry, lascia il resto invariato
    entry_lines = sorted(
        [l for l in body if l.startswith("- [[")],
        key=lambda l: l.lower(),
    )
    other_lines = [l for l in body if not l.startswith("- [[")]
    body = other_lines + entry_lines

    # Garantisce riga vuota dopo header e prima della sezione successiva
    body = [""] + [l for l in body if l.strip()] + [""]

    # Ricostruisce il documento
    new_lines = lines[: section_start + 1] + body + lines[section_end:]

    # Aggiorna la data Last updated
    new_lines = [
        f"**Last updated**: {today_str}" if l.startswith("**Last updated**:") else l
        for l in new_lines
    ]

    home_path.write_text("\n".join(new_lines), encoding="utf-8")


# -----------------------------------------------------------------------
# Source slug e Domains
# -----------------------------------------------------------------------

def source_filename_to_slug(source_file: str) -> str:
    """
    Converte il nome file sorgente in slug wiki.
    Es: 'Software 2.0.md' → 'software-2-0'
        'The Bitter Lesson.md' → 'the-bitter-lesson'
    """
    stem = Path(source_file).stem
    slug = stem.lower()
    slug = re.sub(r"[\s.]+", "-", slug)     # spazi e punti → trattino
    slug = re.sub(r"[^a-z0-9\-]", "", slug) # rimuove caratteri non validi
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def load_domains(cfg: dict) -> list[str]:
    """
    Legge domains.md e ritorna la lista degli slug di dominio esistenti.
    """
    configured_domains = get_domains(cfg)
    if not configured_domains:
        return []
    wiki_path = Path(configured_domains[0]["wiki"])
    domains_path = wiki_path / "domains.md"
    if not domains_path.exists():
        return []
    content = domains_path.read_text(encoding="utf-8")
    return re.findall(r"\[\[([^\]]+)\]\]", content)


def update_domains(new_domains: list[str], cfg: dict) -> list[str]:
    """
    Aggiunge i nuovi slug dominio a domains.md se non già presenti.
    Ritorna la lista dei domini effettivamente aggiunti.
    """
    configured_domains = get_domains(cfg)
    if not configured_domains:
        return []
    wiki_path = Path(configured_domains[0]["wiki"])
    domains_path = wiki_path / "domains.md"
    if not domains_path.exists():
        return []

    content  = domains_path.read_text(encoding="utf-8")
    existing = set(re.findall(r"\[\[([^\]]+)\]\]", content))
    added: list[str] = []

    for slug in new_domains:
        if slug not in existing:
            content = content.rstrip() + f"\n- [[{slug}]]\n"
            existing.add(slug)
            added.append(slug)

    if added:
        # Aggiorna la data Last updated nel file
        today_str = datetime.now().strftime("%Y-%m-%d")
        content = re.sub(
            r"(\*\*Last updated\*\*:).*",
            rf"\1 {today_str}",
            content,
        )
        domains_path.write_text(content, encoding="utf-8")

    return added
