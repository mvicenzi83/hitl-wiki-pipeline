# Workflow — Wiki v2 Implementation

**Scopo**: documentare passo per passo ogni fase del sistema, dalla fonte grezza alla pagina wiki aggiornata.

---

## Panoramica

Il sistema trasforma file Markdown in `/raw` in conoscenza strutturata in `/wiki` attraverso tre fasi:

```
/raw/ai/*.md
     │
     ▼
[1] Estrazione entità (extract_candidates)
     │  → /candidates/*.json
     │
     ▼
[2] Proposta ingest (ingest_assistant)
     │  → /pending_ingests/proposal_*.md
     │
     ▼  ← revisione umana (HITL)
     │
[3] Aggiornamento confidence (confidence_manager)
     │  → frontmatter YAML in /wiki/ai/*.md
     └  → /wiki/ai/health-report.md
```

Tutto può partire manualmente oppure in automatico tramite **`wiki_watcher.py`**, che monitora `/raw/ai` e triggera la pipeline non appena un file viene aggiunto.

---

## Struttura del progetto

```
wiki-v2-implementation/
├── config.toml              ← configurazione centralizzata (path, soglie, parametri)
├── utils.py                 ← libreria condivisa (estrazione, formula, frontmatter)
│
├── extract_candidates.py    ← FASE 1: estrazione entità → JSON
├── ingest_assistant.py      ← FASE 2: proposta wiki → Markdown
├── confidence_manager.py    ← FASE 3: calcolo score + health report
├── wiki_watcher.py          ← orchestratore automatico (file watcher)
│
├── candidates/              ← output FASE 1 (JSON per ogni sorgente)
├── pending_ingests/         ← output FASE 2 (proposal Markdown da revisionare)
│
└── run_watcher.bat          ← lancia wiki_watcher in un click
```

---

## config.toml — unico punto di configurazione

Prima di qualsiasi cosa, tutti i path e i parametri vivono qui.
Non serve toccare gli script per adattarli a un setup diverso.

```toml
[paths]
raw_ai         = "D:/obsidian_git/raw/ai"
wiki_ai        = "D:/obsidian_git/wiki/ai"
proposals_dir  = "D:/obsidian_git/projects/wiki-v2-implementation/pending_ingests"
candidates_dir = "D:/obsidian_git/projects/wiki-v2-implementation/candidates"
health_report  = "D:/obsidian_git/wiki/ai/health-report.md"

[confidence]
threshold      = 1.5   # sotto questo valore: pagina considerata fragile
base_score     = 1.0   # baseline garantita anche a pagine senza citazioni
word_bonus_max = 1.0   # bonus massimo per contenuto sviluppato
word_target    = 300   # parole necessarie per il bonus massimo

[extraction]
min_count_concept = 2  # occorrenze minime per includere un termine kebab-case
min_count_tool    = 1  # occorrenze minime per includere un CamelCase / named entity
```

---

## Fase 1 — Estrazione entità (`extract_candidates.py`)

### Cosa fa

Legge ogni `.md` in `/raw/ai`, estrae entità candidate tramite pattern regex e le serializza in JSON nella cartella `/candidates`.

I tipi di entità rilevati sono tre, con classificazione basata sull'**origine del match**:

| Tipo | Origine del match | Esempi |
| :--- | :--- | :--- |
| `Software/Tool` | Pattern CamelCase | `OpenClaw`, `WebSocket`, `OpenRouter` |
| `Named/Entity` | Pattern contestuale (`using X`, `based on X`) | `LangGraph`, `Qdrant` |
| `Concept/Term` | Pattern kebab-case | `iii-engine`, `agent-memory`, `real-time` |

Il filtro `is_technical_noise()` scarta automaticamente variabili CSS (`--color-*`), parametri API (`.*-scheme`, `.*-attr`), prefissi generici (`well-`, `non-`, `high-`) e qualsiasi termine a 4+ parti kebab (che indica quasi sempre un parametro, non un concetto).

### Esecuzione

```bash
# Tutti i file in /raw/ai
python extract_candidates.py

# Singolo file (es. passato dal watcher)
python extract_candidates.py "D:/obsidian_git/raw/ai/Agentmemory.md"
```

### Output di esempio — `candidates/candidates_Agentmemory.json`

```json
{
    "source_file": "Agentmemory.md",
    "candidates": [
        { "entity": "iii-engine",   "type": "Concept/Term",  "count": 13 },
        { "entity": "OpenClaw",     "type": "Software/Tool", "count": 7  },
        { "entity": "OpenCode",     "type": "Software/Tool", "count": 5  },
        { "entity": "SessionStart", "type": "Software/Tool", "count": 5  },
        { "entity": "WebSocket",    "type": "Software/Tool", "count": 3  }
    ],
    "text_preview": "<p align=\"center\">..."
}
```

Il JSON è il **contesto che viene passato all'LLM** (ponte verso la Fase successiva, ancora manuale).

---

## Fase 2 — Proposta ingest (`ingest_assistant.py`)

### Cosa fa

Per ogni sorgente genera un file Markdown in `/pending_ingests` che elenca le entità estratte con il loro **stato rispetto alla wiki esistente**:

- **Già esistente** — la pagina wiki c'è già con nome identico → aggiornare quella
- **Sospetto duplicato** — match fuzzy con un file esistente (`difflib`, cutoff 0.85) → verificare se è un alias
- **Nuova pagina** — nessun match → creare la pagina

### Esecuzione

```bash
# Tutti i file
python ingest_assistant.py

# Singolo file
python ingest_assistant.py "D:/obsidian_git/raw/ai/Agentmemory.md"
```

### Output di esempio — `pending_ingests/proposal_Agentmemory.md`

```markdown
# Ingest Proposta: Agentmemory.md
**Sorgente**: `D:\obsidian_git\raw\ai\Agentmemory.md`

## Entità Rilevate

- [ ] [[iii-engine]]    (Tipo: Concept/Term)  → *Già esistente (freq: 13)*
- [ ] [[OpenClaw]]      (Tipo: Software/Tool) → *Già esistente (freq: 7)*
- [ ] [[SessionStart]]  (Tipo: Software/Tool) → *Nuova pagina (freq: 5)*
- [ ] [[WebSocket]]     (Tipo: Software/Tool) → *Già esistente (freq: 3)*
- [ ] [[agent-memory]]  (Tipo: Concept/Term)  → *Già esistente (freq: 2)*
- [ ] [[google-gemini]] (Tipo: Concept/Term)  → *Nuova pagina (freq: 2)*
...

## Sintesi Suggerita

```
<p align="center">
  <img src="assets/banner.png" ...>
...
```

---
*Proposal generata automaticamente — richiede revisione umana prima dell'ingest.*
```

### Human-in-the-Loop (HITL)

Questo file è la **checkpoint umana** della pipeline. Prima dell'ingest effettivo:

1. Aprire la proposal in Obsidian
2. Spuntare le caselle `[ ]` delle entità da processare
3. Decidere per ciascuna: aggiornare pagina esistente / creare nuova / ignorare
4. Procedere manualmente all'aggiornamento delle pagine wiki corrispondenti

Nessuna modifica alla wiki avviene senza approvazione esplicita.

---

## Fase 3 — Confidence Manager (`confidence_manager.py`)

### Cosa fa

Scansiona tutte le pagine in `/wiki/ai`, calcola un **punteggio di confidenza** per ognuna e:
1. Aggiorna il frontmatter YAML di ogni pagina con `confidence_score` e `last_analyzed`
2. Genera `/wiki/ai/health-report.md` con le pagine fragili e i pilastri top-5

### Formula

$$C = \frac{n_{citazioni} + \overbrace{(base\_score + \min(word\_bonus\_max,\ \frac{n_{parole}}{word\_target}))}^{\text{content score}}}{1 + \ln(1 + \Delta t_{giorni})}$$

**Parametri** (tutti configurabili in `config.toml`):

| Parametro | Default | Effetto |
| :--- | :---: | :--- |
| `base_score` | 1.0 | Baseline: nessuna pagina parte da 0.0 |
| `word_bonus_max` | 1.0 | Bonus max per contenuto sviluppato |
| `word_target` | 300 | Parole necessarie per il bonus massimo |
| `threshold` | 1.5 | Soglia "fragile" nel health report |

**Esempi calibrati:**

| Scenario | C |
| :--- | :---: |
| Pagina nuova, oggi, 0 citazioni, 50 parole | ~1.17 → fragile |
| Pagina nuova, oggi, 0 citazioni, 300 parole | ~2.0 → sana |
| 5 citazioni, aggiornata 3 mesi fa | ~2.5 → solida |
| 15+ citazioni, aggiornata di recente | 10+ → pilastro |

### Esecuzione

```bash
python confidence_manager.py
```

### Output di esempio — `wiki/ai/health-report.md`

```markdown
# Wiki Health Report
**Last analyzed**: 2026-04-22

## Zone Critiche (confidence < 1.5)

| Pagina         | Confidence | Citazioni | Ultimo Aggiornamento |
| :---           | :---:      | :---:     | :---:                |
| [[openclaw]]   | 1.197      | 0         | 2026-04-22           |
| [[opencode]]   | 1.197      | 0         | 2026-04-22           |
| [[websocket]]  | 1.22       | 0         | 2026-04-22           |

## Pilastri della Conoscenza (Top 5)

1. [[llm-wiki-pattern]]  (Conf: 17.0)
2. [[rag]]               (Conf: 14.527)
3. [[llm-wiki-v2]]       (Conf: 10.887)
4. [[agent-memory]]      (Conf: 9.637)
5. [[vector-database]]   (Conf: 8.607)
```

### Frontmatter iniettato in ogni pagina wiki

```yaml
---
confidence_score: 9.637
last_analyzed: 2026-04-22
---
```

Questi metadati sono leggibili direttamente in Obsidian nelle properties della nota.

---

## Modalità automatica — `wiki_watcher.py`

Il watcher mette insieme le tre fasi in un loop continuo.

```bash
# Avvio manuale
python wiki_watcher.py

# Oppure doppio click su
run_watcher.bat
```

**Flusso automatico:**

```
utente copia/salva file.md in /raw/ai
         │
         ▼
wiki_watcher rileva evento on_created / on_moved
         │
         ▼
ingest_assistant.py "percorso/file.md"   ← solo questo file, non tutta la dir
         │
         ▼
proposal_file.md creata in /pending_ingests
         │
         ▼
confidence_manager.py                    ← aggiorna tutta la wiki + health report
         │
         ▼
utente apre la proposal in Obsidian e decide cosa fare (HITL)
```

L'output del watcher a console è:

```
Wiki Watcher avviato
Monitoring: D:\obsidian_git\raw\ai
------------------------------------------------------
1. Aggiungi un .md in /raw/ai  -> Ingest Assistant (solo quel file)
2. Dopo ogni ingest            -> Confidence Manager si aggiorna
------------------------------------------------------

[+] Nuovo file rilevato: nuovo-paper.md
    Avvio Ingest Assistant per questo file...
    Proposal generata per: nuovo-paper.md
    Aggiornamento confidence scores...
    Health report aggiornato.
```

---

## Referimento rapido — comandi

| Obiettivo | Comando |
| :--- | :--- |
| Analizza tutti i raw | `python extract_candidates.py` |
| Analizza un file | `python extract_candidates.py "D:/.../file.md"` |
| Genera tutte le proposal | `python ingest_assistant.py` |
| Genera proposal per un file | `python ingest_assistant.py "D:/.../file.md"` |
| Aggiorna confidence + report | `python confidence_manager.py` |
| Avvia il watcher | `python wiki_watcher.py` |

---

## Fasi future (dal roadmap v2)

Il refactor attuale copre le **Fasi 1 e 2** del roadmap. Le prossime evoluzioni pianificate:

- **Fase 3** — Knowledge Graph (Neo4j/FalkorDB) per relazioni tipizzate tra pagine
- **Fase 3** — Ricerca ibrida BM25 + vettoriale
- **Fase 4** — Retention Engine (curva di dimenticanza di Ebbinghaus)
- **Fase 4** — Self-Healing agent per link rotti e contraddizioni
- **Ponte LLM** — passare i JSON di `extract_candidates` a un modello (es. via OpenRouter) per generare bozze di pagine wiki in automatico, sempre con HITL finale

---

*Generato il 2026-04-22 — basato sullo stato corrente degli script dopo il refactor v2.*
