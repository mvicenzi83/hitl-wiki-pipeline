# Wiki Maintenance Script — Piano di Implementazione

**Data**: 2026-05-01
**Scopo**: Briefing completo per implementare `pipeline/wiki_maintenance.py`

---

## Contesto del progetto

Wiki personale in stile LLM Wiki (pattern Karpathy), gestita tramite una pipeline
Python semi-automatica con gate HITL (human-in-the-loop).

### Struttura cartelle rilevante

```
wiki/
  ai/
    domains.md                        ← vocabolario controllato dei domini semantici
    health-report.md                  ← generato da confidence_manager.py
    software-2-0.md                   ← source page (Karpathy)
    the-bitter-lesson.md              ← source page (Sutton)
    computing-machenery-and-intelligence.md  ← source page (Turing)
    alphago.md                        ← concept page
    convnet.md                        ← concept page
    imagenet.md                       ← concept page
    wavenet.md                        ← concept page
    human-knowledge-based.md          ← concept page
    discrete-state.md                 ← concept page
    wheels-and-cards.md               ← concept page

raw/
  ai/
    Software 2.0.md                   ← sorgente immutabile
    The Bitter Lesson.md              ← sorgente immutabile
    Computing machenery and intelligence.md  ← sorgente immutabile

pipeline/
  config.toml                         ← configurazione centralizzata
  utils.py                            ← libreria condivisa
  llm_bridge.py                       ← client LLM + prompt templates
  confidence_manager.py               ← calcola confidence score e genera health-report
  wiki_maintenance.py                 ← DA CREARE
```

### Struttura delle pagine wiki (formato standard)

```markdown
---
confidence_score: 1.793
last_analyzed: 2026-05-01
---

# Titolo Pagina
**Summary**: Una o due frasi.

**Domain**: ai

**Sources**: [[source-slug]]         ← per concept pages
**Sources**: `raw/ai/filename.md`    ← per source pages (testo plain, non link)

**Domains**: [[dom-1]], [[dom-2]]    ← solo nelle source pages

**Last updated**: YYYY-MM-DD

---

Corpo testo...

## Related pages
- [[pagina-correlata]]
```

**Tipi di pagina:**
- **source page**: ha `**Domains**:` e `**Sources**:` come path raw in backtick
- **concept page**: ha `**Sources**:` come `[[wiki-link]]` alla source page
- **domains.md**: lista dei domini semantici, escluso dai check di salute
- **health-report.md**: generato automaticamente, escluso dai check

---

## Funzioni utils.py già disponibili

```python
load_config() -> dict
get_domains(cfg) -> list[dict]           # domini configurati in config.toml
get_wiki_files(wiki_path) -> list[str]   # stem dei file .md
load_domains(cfg) -> list[str]           # slug da domains.md
source_filename_to_slug(filename) -> str # "Software 2.0.md" → "software-2-0"
calculate_confidence(...)                # formula confidence
clean_and_update_properties(...)         # aggiorna frontmatter YAML
update_wiki_home(slug, summary, domain, cfg)
update_domains(new_domains, cfg) -> list[str]
```

Il client LLM è in `llm_bridge.py → call_llm(prompt, cfg)`.
Supporta sia Ollama locale che qualsiasi endpoint OpenAI-compatible.
Configurazione in `config.toml [llm]`.

---

## Specifiche di `wiki_maintenance.py`

### Principio fondamentale
Lo script **produce un report, non modifica nulla**. Coerente con l'approccio HITL.
Le correzioni vengono decise dall'utente dopo aver letto il report.

### CLI

```
python wiki_maintenance.py              # check strutturali (no LLM)
python wiki_maintenance.py --full       # + analisi LLM (accuracy + link impliciti)
python wiki_maintenance.py --page alphago  # analisi di una singola pagina
```

### Fasi di esecuzione

#### Fasi Python-pure (sempre attive)

**Fase 1 — Broken links**
- Legge tutti i `[[link]]` nel testo di ogni pagina
- Verifica che esista un file `.md` corrispondente in `wiki/ai/`
- Esclude: `domains.md`, `health-report.md`
- Output: lista `[[link]] in pagina X → nessuna pagina corrispondente`

**Fase 2 — Pagine orfane**
- Conta le citazioni inbound per ogni pagina (già fa questo il confidence_manager)
- Segnala pagine con 0 citazioni che non siano source pages o domains
- Suggerisce se aggiungere link da pagine correlate

**Fase 3 — Bidirezionalità**
- Se la pagina A cita `[[B]]` nei Related pages, controlla che B citi `[[A]]`
- Non è obbligatoria ma è una best practice da segnalare

**Fase 4 — Source pages senza Domains**
- Cerca pagine con `**Sources**: \`raw/...` (pattern source page)
- Verifica che abbiano il campo `**Domains**:`
- Segnala quelle che ne sono prive

**Fase 5 — Concept pages senza source page**
- Verifica che ogni `[[source-slug]]` citato in `**Sources**:` abbia una pagina esistente
- Caso tipico: concept page generata prima che la source page fosse creata

#### Fasi LLM (solo con `--full`)

**Fase 6 — Accuratezza**
Per ogni concept page che ha una source page collegata:
- Legge il testo della pagina wiki
- Legge l'excerpt del raw corrispondente
- Chiede all'LLM: "Ci sono affermazioni nella pagina wiki che contraddicono o non sono supportate dalla sorgente?"
- Riporta solo le discrepanze trovate, non i "tutto ok"

**Fase 7 — Link impliciti mancanti**
Per ogni pagina:
- Chiede all'LLM: "Nel testo ci sono concetti che vengono citati senza diventare [[wiki-link]] ma che hanno già una pagina in wiki?"
- Passa la lista delle pagine esistenti come contesto
- Riporta i suggerimenti come: `"Max Pooling" citato in [[convnet]] → potrebbe diventare [[max-pooling]]`

### Output: `wiki/ai/maintenance-report.md`

Formato:

```markdown
# Wiki Maintenance Report
**Generato**: YYYY-MM-DD HH:MM
**Modalità**: full | light

---

## 🔴 Priorità Alta

### Broken Links
- [[alphago]]: cita [[reinforcement-learning]] → pagina mancante
- ...

### Concept pages senza source page
- [[discrete-state]]: Sources [[computing-machenery-and-intelligence]] → OK ✓
- ...

---

## 🟡 Attenzione

### Bidirezionalità incompleta
- [[software-2-0]] linka [[convnet]] ma [[convnet]] non ha [[software-2-0]] in Related pages

### Source pages senza Domains
- ...

---

## 🟢 Info

### Pagine orfane
- [[domains]] — by design (escludi da futuri report con flag)

---

## 🤖 Analisi LLM (solo --full)

### Accuratezza
- [[alphago]]: "L'affermazione X non è supportata dal testo sorgente"

### Link impliciti suggeriti
- [[convnet]]: "Max Pooling" → [[max-pooling]] (pagina mancante — crea stub?)

---

## Azioni Suggerite

- [ ] Crea stub page per [[reinforcement-learning]]
- [ ] Aggiorna Related pages di [[convnet]] → aggiungi [[software-2-0]]
- [ ] Verifica affermazione in [[alphago]] vs raw
```

### File da escludere sempre
```python
EXCLUDED_PAGES = {"health-report", "maintenance-report", "domains"}
```

---

## Integrazione con il sistema esistente

### In `config.toml` aggiungere (sezione `[maintenance]`)

```toml
[maintenance]
# Pagine escluse dai check (nomi file senza estensione)
excluded_pages = ["health-report", "maintenance-report", "domains"]

# Quanti caratteri del raw passare all'LLM per il check di accuratezza
accuracy_excerpt_chars = 4000

# Output del report
report_path = "D:/obsidian_git/wiki/ai/maintenance-report.md"
```

### In `wiki_watcher.py`
Opzionale: il watcher potrebbe triggerare il check light (`--light`) automaticamente
ogni volta che una pagina viene modificata. Da valutare se utile o invasivo.

---

## Considerazioni implementative

1. **Riuso di `call_llm()`**: il client LLM è già in `llm_bridge.py`, importarlo direttamente.

2. **Riuso della logica inbound citations**: `confidence_manager.py` costruisce già `citation_map`. Valutare se estrarre quella logica in `utils.py` invece di duplicarla.

3. **Idempotenza**: il report viene sovrascritto ad ogni run — nessuna gestione di stato necessaria.

4. **Ordinamento dei problemi**: i problemi vanno raggruppati per severità (🔴 > 🟡 > 🟢), non per pagina. Rende il report più actionable.

5. **Dry-run safe**: lo script non deve mai scrivere nei file wiki, solo in `maintenance-report.md`.

---

## Stato della wiki al momento del piano (2026-05-01)

**Source pages** (3): `software-2-0`, `the-bitter-lesson`, `computing-machenery-and-intelligence`

**Concept pages** (7): `alphago`, `convnet`, `imagenet`, `wavenet`, `human-knowledge-based`, `discrete-state`, `wheels-and-cards`

**Altri file wiki**: `domains.md`, `health-report.md`, `wiki-home.md` (root)

**Broken links noti** (da risolvere nella nuova sessione o a run dello script):
`[[Software 2.0]]`, `[[Deep Learning]]`, `[[Reinforcement Learning]]`, `[[Neural Networks]]`,
`[[The Bitter Lesson]]`, `[[computational-scale]]`, `[[state-space search]]`,
`[[finite-state machine]]`, `[[symbolic-ai]]`, `[[computing]]`, `[[mechanical intelligence]]`,
`[[information retrieval]]`, `[[computing machinery]]`, `[[Generative Models]]`

---

*Piano redatto nella sessione 2026-05-01. File di riferimento: `AGENTS.md`, `pipeline/README.md`.*
