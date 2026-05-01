"""
llm_bridge.py — Ponte LLM: genera bozze di pagine wiki partendo dai candidates JSON.

Per ogni entita' candidata (top N per source, configurabile):
  - Se la pagina esiste gia' in wiki: genera una bozza di aggiornamento
  - Se la pagina e' nuova: genera una bozza completa da zero

Le bozze vengono salvate in /drafts e devono essere approvate tramite approve_drafts.py
prima di entrare in /wiki/ai.

Supporta qualsiasi endpoint OpenAI-compatible:
  - Ollama locale  (base_url = http://localhost:11434/v1)
  - Google AI Studio
  - OpenRouter
  - Qualsiasi altro provider compatibile

Usage:
    python llm_bridge.py                        # Processa tutti i candidates JSON
    python llm_bridge.py candidates/candidates_Agentmemory.json   # Singolo file
"""

import json
import os
import sys
import time
import requests
from pathlib import Path

from utils import load_config, get_wiki_files, find_best_match, get_domains, source_filename_to_slug, load_domains

# -----------------------------------------------------------------------
# Prompt templates
# -----------------------------------------------------------------------

PROMPT_NEW_PAGE = """\
Sei un assistente specializzato nella scrittura di pagine in una knowledge base Markdown personale (stile LLM Wiki di Andrej Karpathy).

Devi creare una pagina wiki per il concetto: **{entity}** (tipo: {etype})

--- CONTESTO DALLA SORGENTE ---
{source_excerpt}
--- FINE CONTESTO ---

Scrivi la pagina seguendo ESATTAMENTE questo formato (non aggiungere altro):

# {entity}
**Summary**: [una o due frasi che definiscono il concetto in modo preciso]

**Domain**: ai

**Sources**: [[{source_slug}]]

**Last updated**: {today}

---

[Corpo della pagina: 150-300 parole. Usa sotto-sezioni se utile. Cita fatti concreti dal contesto. Non inventare.]

## Related pages

- [[link-a-pagina-correlata-1]]
- [[link-a-pagina-correlata-2]]

Usa [[wiki-links]] per collegare concetti correlati. Scrivi solo la pagina, niente altro.
"""

PROMPT_UPDATE_PAGE = """\
Sei un assistente specializzato nell'aggiornamento di pagine in una knowledge base Markdown personale (stile LLM Wiki di Andrej Karpathy).

Devi aggiornare la pagina wiki esistente per: **{entity}**

--- PAGINA ATTUALE ---
{existing_content}
--- FINE PAGINA ATTUALE ---

--- NUOVO CONTESTO DALLA SORGENTE ([[{source_slug}]]) ---
{source_excerpt}
--- FINE NUOVO CONTESTO ---

Istruzioni:
1. Mantieni la struttura e il formato esistente della pagina
2. Integra le nuove informazioni senza duplicare quelle gia' presenti
3. Aggiorna 'Sources' aggiungendo '[[{source_slug}]]' se non c'e' gia'
4. Aggiorna 'Last updated' a {today}
5. Aggiungi eventuali nuovi [[wiki-links]] se pertinenti
6. NON rimuovere informazioni esistenti valide

Scrivi la pagina aggiornata completa (non un diff), niente altro.
"""

PROMPT_SOURCE_PAGE = """\
Sei un assistente specializzato nella scrittura di pagine in una knowledge base Markdown personale (stile LLM Wiki di Andrej Karpathy).

Devi creare la PAGINA SORGENTE per il documento: **{source_title}**
Questa pagina rappresenta il documento originale nella wiki e collega le pagine concetto estratte.

--- TESTO DELLA SORGENTE ---
{source_text}
--- FINE TESTO ---

--- DOMINI SEMANTICI ESISTENTI ---
{existing_domains}
--- FINE DOMINI ---

--- PAGINE CONCETTO GIA' ESTRATTE DA QUESTA SORGENTE ---
{child_pages_list}
--- FINE PAGINE CONCETTO ---

Scrivi la pagina seguendo ESATTAMENTE questo formato (non aggiungere altro):

# {source_title}
**Summary**: [una o due frasi che sintetizzano la tesi centrale del documento]

**Domain**: {domain_name}

**Sources**: `raw/{domain_name}/{source_file}`

**Domains**: [[dominio-1]], [[dominio-2]]

**Last updated**: {today}

---

[Sintesi del documento: 150-250 parole. Argomenta la tesi principale e i contributi chiave. Non ripetere i dettagli gia' nelle pagine concetto.]

## Pages from this source

{child_pages_list}

## Related pages

- [[pagina-correlata]]

Regole per **Domains**:
- Scegli 2-5 domini tra quelli ESISTENTI sopra che siano semanticamente rilevanti.
- Se nessun dominio esistente e' adeguato, proponi nuovi slug kebab-case (es. [[natural-language-processing]]).
- Non usare il titolo del documento come dominio.
- Scrivi solo la pagina, nient'altro.
"""

def call_llm(prompt: str, cfg: dict) -> str:
    """
    Chiama il provider LLM con il prompt fornito.
    Supporta due formati:
      - "ollama"  : API nativa Ollama (/api/chat) - raccomandata per uso locale
      - "openai"  : Endpoint OpenAI-compatible (/v1/chat/completions) per provider cloud

    Ritorna il testo della risposta o lancia un'eccezione.
    """
    llm_cfg    = cfg["llm"]
    base_url   = llm_cfg["base_url"].rstrip("/")
    model      = llm_cfg["model"]
    api_format = llm_cfg.get("api_format", "openai")
    api_key_env = llm_cfg.get("api_key_env", "")

    # Legge la chiave API dalla variabile d'ambiente (mai hardcoded)
    api_key = os.environ.get(api_key_env, "ollama") if api_key_env else "ollama"

    if api_format == "ollama":
        # API nativa Ollama: piu' stabile, non richiede chiave API
        url = f"{base_url}/api/chat"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "num_predict": llm_cfg.get("max_tokens", 1500),
                "temperature": llm_cfg.get("temperature", 0.3),
            },
        }
        response = requests.post(url, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()

    else:
        # Formato OpenAI-compatible: per Google AI Studio, OpenRouter, ecc.
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": llm_cfg.get("max_tokens", 1500),
            "temperature": llm_cfg.get("temperature", 0.3),
            "stream": False,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()


# -----------------------------------------------------------------------
# Logica principale
# -----------------------------------------------------------------------

def generate_draft_for_entity(
    entity: str,
    etype: str,
    source_file: str,
    source_excerpt: str,
    today_str: str,
    cfg: dict,
    existing_wiki: list[str],
    wiki_path: Path,
    drafts_dir: Path,
) -> Path | None:
    """
    Genera una bozza per una singola entità. Ritorna il path della bozza o None se skippata.
    Estratta da process_candidates_file() per permettere l'importazione da process_proposal.py.
    """
    page_slug   = entity.lower().replace(" ", "-")
    source_slug = source_filename_to_slug(source_file)
    draft_path  = drafts_dir / f"draft_{page_slug}.md"

    if draft_path.exists():
        print(f"  [skip] Bozza gia' esistente per: {entity}")
        return None

    match, is_exact = find_best_match(entity, existing_wiki)

    print(f"  [LLM] Generazione bozza per: {entity} ({etype})...", end=" ", flush=True)
    t0 = time.time()

    if is_exact and match:
        existing_content = (wiki_path / f"{match}.md").read_text(encoding="utf-8")
        prompt = PROMPT_UPDATE_PAGE.format(
            entity=entity,
            existing_content=existing_content,
            source_slug=source_slug,
            source_excerpt=source_excerpt,
            today=today_str,
        )
        draft_type = "update"
    else:
        prompt = PROMPT_NEW_PAGE.format(
            entity=entity,
            etype=etype,
            source_slug=source_slug,
            source_excerpt=source_excerpt,
            today=today_str,
        )
        draft_type = "new"

    try:
        llm_output = call_llm(prompt, cfg)
    except requests.exceptions.HTTPError as e:
        print(f"\n  [!] Errore HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except KeyError as e:
        print(f"\n  [!] Risposta LLM inattesa (chiave mancante: {e})")
        return None
    except Exception as e:
        if isinstance(e, requests.exceptions.ConnectionError):
            raise
        print(f"\n  [!] Errore: {e}")
        return None

    metadata_header = (
        f"<!--\n"
        f"BOZZA GENERATA AUTOMATICAMENTE - RICHIEDE REVISIONE\n"
        f"Tipo:       {draft_type}\n"
        f"Entita':    {entity}\n"
        f"Tipo ent.:  {etype}\n"
        f"Sorgente:   {source_file}\n"
        f"Generato:   {today_str}\n"
        f"Wiki match: {match if match else 'nessuno'}\n"
        f"-->\n\n"
    )

    sentinel_block = (
        "\n\n---\n"
        "*Bozza generata automaticamente \u2014 richiede revisione prima della pubblicazione.*\n\n"
        "- [ ] \u2705 APPROVA BOZZA \u2014 spunta per pubblicare in wiki/ai\n"
    )

    draft_path.write_text(metadata_header + llm_output + sentinel_block, encoding="utf-8")
    elapsed = round(time.time() - t0, 1)
    print(f"[OK] ({draft_type}, {elapsed}s)")
    return draft_path


def generate_source_page(
    source_file: str,
    source_text: str,
    child_slugs: list[str],
    today_str: str,
    cfg: dict,
    wiki_path: Path,
    drafts_dir: Path,
    domain_name: str,
) -> Path | None:
    """
    Genera la bozza della pagina sorgente per un documento raw.
    Deve essere chiamata prima di generate_draft_for_entity().
    Ritorna il path della bozza o None se gia' esistente.
    """
    source_slug  = source_filename_to_slug(source_file)
    source_title = Path(source_file).stem
    draft_path   = drafts_dir / f"draft_{source_slug}.md"

    if draft_path.exists():
        print(f"  [skip] Bozza source page gia' esistente: {source_file}")
        return None

    wiki_page = wiki_path / f"{source_slug}.md"
    if wiki_page.exists():
        print(f"  [skip] Source page gia' in wiki: {source_slug}.md")
        return None

    # Combina pagine concetto passate + eventuali gia' in wiki per questa sorgente
    existing_children = [
        f.stem for f in wiki_path.glob("*.md")
        if f.stem not in ("health-report", "domains", source_slug)
        and f"[[{source_slug}]]" in f.read_text(encoding="utf-8")
    ]
    all_children = sorted(set(existing_children + child_slugs))
    child_pages_list = "\n".join(f"- [[{p}]]" for p in all_children) if all_children else "*(nessuna ancora)*"

    existing_domains = load_domains(cfg)
    domains_block = (
        "\n".join(f"- [[{d}]]" for d in existing_domains)
        if existing_domains
        else "(nessuno ancora — proponi nuovi slug kebab-case)"
    )

    llm_cfg = cfg["llm"]
    # Per la source page usa un excerpt piu' ampio (2x rispetto alle concept pages)
    excerpt_chars = llm_cfg.get("source_excerpt_chars", 3000) * 2
    source_excerpt = source_text[:excerpt_chars]

    prompt = PROMPT_SOURCE_PAGE.format(
        source_title=source_title,
        source_file=source_file,
        source_text=source_excerpt,
        existing_domains=domains_block,
        child_pages_list=child_pages_list,
        domain_name=domain_name,
        today=today_str,
    )

    print(f"  [LLM] Generazione source page per: {source_file}...", end=" ", flush=True)
    t0 = time.time()

    try:
        llm_output = call_llm(prompt, cfg)
    except requests.exceptions.HTTPError as e:
        print(f"\n  [!] Errore HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        print(f"\n  [!] Errore: {e}")
        return None

    metadata_header = (
        f"<!--\n"
        f"BOZZA PAGINA SORGENTE - RICHIEDE REVISIONE\n"
        f"Tipo:       source_page\n"
        f"Sorgente:   {source_file}\n"
        f"Generato:   {today_str}\n"
        f"-->\n\n"
    )

    sentinel_block = (
        "\n\n---\n"
        "*Bozza source page generata automaticamente \u2014 richiede revisione prima della pubblicazione.*\n\n"
        "- [ ] \u2705 APPROVA BOZZA \u2014 spunta per pubblicare in wiki/ai\n"
    )

    draft_path.write_text(metadata_header + llm_output + sentinel_block, encoding="utf-8")
    elapsed = round(time.time() - t0, 1)
    print(f"[OK] ({elapsed}s)")
    return draft_path


def process_candidates_file(candidates_path: Path, cfg: dict) -> int:
    """
    Processa un singolo file candidates JSON.
    Ritorna il numero di bozze generate.
    """
    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")

    llm_cfg    = cfg["llm"]
    paths      = cfg["paths"]
    max_ent    = llm_cfg.get("max_entities_per_source", 5)
    excerpt_chars = llm_cfg.get("source_excerpt_chars", 3000)
    drafts_dir = Path(llm_cfg.get("drafts_dir", paths.get("drafts_dir", "")))

    drafts_dir.mkdir(parents=True, exist_ok=True)

    # Carica candidates JSON
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    source_file = data["source_file"]
    candidates  = data["candidates"][:max_ent]

    # Risolve wiki_path e raw_path dal campo domain (fallback al primo dominio)
    domain_name = data.get("domain")
    domains     = get_domains(cfg)
    domain      = next((d for d in domains if d["name"] == domain_name), domains[0] if domains else None)
    if domain is None:
        print(f"  [!] Nessun dominio configurato. Skip.")
        return 0
    wiki_path = Path(domain["wiki"])
    raw_path  = Path(domain["raw"])

    if not candidates:
        print(f"  Nessun candidato in {candidates_path.name}, skip.")
        return 0

    # Legge il testo sorgente completo (cerca anche in sottodirectory)
    source_full_path = raw_path / source_file
    if not source_full_path.exists():
        found = list(raw_path.glob(f"**/{source_file}"))
        source_full_path = found[0] if found else source_full_path
    if not source_full_path.exists():
        print(f"  [!] Sorgente non trovata: {source_full_path}, skip.")
        return 0

    source_text    = source_full_path.read_text(encoding="utf-8")
    source_excerpt = source_text[:excerpt_chars]

    # Lista pagine wiki esistenti per deduplication
    existing_wiki = get_wiki_files(wiki_path)

    generated = 0
    for item in candidates:
        entity = item["entity"]
        etype  = item["type"]
        try:
            result = generate_draft_for_entity(
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
            if result is not None:
                generated += 1
        except requests.exceptions.ConnectionError:
            print(f"\n  [!] Impossibile connettersi a {llm_cfg['base_url']}")
            print(f"      Assicurati che Ollama sia in esecuzione (ollama serve)")
            break

    return generated


def run_bridge(cfg: dict, target_file: Path | None = None) -> int:
    """
    Processa tutti i candidates JSON oppure un singolo file.
    Ritorna il totale delle bozze generate.
    """
    candidates_dir = Path(cfg["paths"]["candidates_dir"])

    if target_file:
        files = [target_file]
    else:
        files = sorted(candidates_dir.glob("candidates_*.json"))

    if not files:
        print("Nessun file candidates trovato.")
        return 0

    total = 0
    for cf in files:
        print(f"\nProcessing: {cf.name}")
        total += process_candidates_file(cf, cfg)

    return total


if __name__ == "__main__":
    cfg = load_config()

    print("LLM Bridge avviato")
    print(f"  Provider: {cfg['llm']['base_url']}")
    print(f"  Modello:  {cfg['llm']['model']}")
    print(f"  Max entita' per sorgente: {cfg['llm']['max_entities_per_source']}")
    print()

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if not target.exists():
            print(f"File non trovato: {target}")
            sys.exit(1)
        total = run_bridge(cfg, target_file=target)
    else:
        total = run_bridge(cfg)

    drafts_dir = Path(cfg["llm"]["drafts_dir"])
    print(f"\n{total} bozze generate in: {drafts_dir}")
    print("Usa 'python approve_drafts.py' per revisionarle.")
