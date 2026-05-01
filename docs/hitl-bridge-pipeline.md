---
created: 2026-04-22
status: plan — pending implementation
---

# HITL Checkbox Bridge — Pipeline di Implementazione

## Scopo di questo documento

Questo documento è il piano operativo completo per l'implementazione del sistema **HITL Checkbox Bridge** nella wiki v2. È destinato all'agente di coding che deve eseguire le modifiche.

Descrive: il sistema esistente, il problema da risolvere, la soluzione progettata con i due gate checkpoint, i due nuovi script da creare (`process_proposal.py` e `approve_draft_auto.py`), le quattro modifiche agli script esistenti, e il flusso completo end-to-end con checkpoint di verifica. Ogni sezione contiene informazioni sufficienti per procedere direttamente all'implementazione senza ulteriore analisi.

---

## Sistema Esistente

### Struttura cartelle

```
obsidian_git/
  raw/ai/               ← fonti grezze (immutabili)
  wiki/ai/              ← pagine wiki finali
  projects/wiki-v2-implementation/
    config.toml
    utils.py
    extract_candidates.py
    ingest_assistant.py
    llm_bridge.py
    confidence_manager.py
    approve_drafts.py       ← HITL terminale (già funzionante, rimane invariato)
    wiki_watcher.py
    candidates/             ← output JSON fase 1
    pending_ingests/        ← proposal Markdown da revisionare
    drafts/                 ← bozze generate da LLM
      approved/             ← bozze pubblicate
```

### Script esistenti — cosa fanno

| Script | Ruolo |
|:---|:---|
| `extract_candidates.py` | Legge `.md` da `/raw/ai`, estrae entità (CamelCase, kebab-case, pattern contestuale) e produce `candidates/candidates_NomeSorgente.json` |
| `ingest_assistant.py` | Legge candidates JSON e genera `pending_ingests/proposal_*.md` con checkbox `- [ ] [[EntityName]]` per ogni entità, indicando stato wiki |
| `llm_bridge.py` | Legge candidates JSON, chiama LLM (Ollama locale o OpenAI-compatible) e genera bozze complete in `drafts/draft_slug.md` |
| `confidence_manager.py` | Calcola confidence score per ogni pagina wiki, aggiorna frontmatter YAML e genera `health-report.md` |
| `approve_drafts.py` | Script interattivo da terminale: mostra bozze e chiede [a]pprova / [r]ifiuta / [s]alta. **Rimane invariato** — fallback manuale opzionale |
| `wiki_watcher.py` | Monitora `/raw/ai` con watchdog; su `on_created`/`on_moved` lancia `ingest_assistant.py` poi `confidence_manager.py` |
| `utils.py` | Libreria condivisa: `load_config()`, `extract_entities()`, `find_best_match()` (fuzzy 0.85), `calculate_confidence()`, `clean_and_update_properties()` |
| `config.toml` | Unico punto di configurazione: paths, soglie confidence, parametri estrazione, config LLM |

### Stato del watcher attuale

`wiki_watcher.py` registra **un solo observer** su `/raw/ai`. Non monitora `pending_ingests/` né `drafts/`. Il gap è qui: le checkbox spuntate dall'utente in Obsidian non producono alcuna azione automatica.

---

## Il Problema

Il flusso si interrompe dopo la generazione della proposal:

```
pending_ingests/proposal_*.md  →  ???  →  wiki/ai/*.md
```

Dopo che l'utente revisiona la proposal in Obsidian, non esiste un meccanismo automatico per:

1. Sapere quali entità l'utente ha approvato (le checkbox sono puramente visive)
2. Invocare `llm_bridge.py` per generare le bozze solo di quelle entità
3. Pubblicare le bozze approvate in `wiki/ai/` senza aprire un terminale

---

## Soluzione: HITL Checkbox Bridge

### Principio

Due **gate espliciti** basati su checkbox native di Obsidian. In cima (o in fondo) a ogni documento gestito dal sistema viene aggiunta una riga **sentinel**. Il watcher rileva il salvataggio del file e controlla: se la sentinel è spuntata e il file non è già stato processato → esegue l'azione corrispondente.

### Gate 1 — Proposal (`pending_ingests/`)

`ingest_assistant.py` aggiunge **in cima alla sezione entità**, prima del primo `- [ ] [[...]]`:

```
- [ ] ✅ AVVIA INGEST — spunta dopo aver selezionato le entità sopra
```

**Azione utente**: apre la proposal in Obsidian, spunta le entità volute + questa sentinel, salva.

**Azione watcher**: rileva `on_modified` in `pending_ingests/`, lancia `process_proposal.py <path>`.

### Gate 2 — Bozza (`drafts/`)

`llm_bridge.py` aggiunge **in fondo** ad ogni bozza generata:

```markdown
---
*Bozza generata automaticamente — richiede revisione prima della pubblicazione.*

- [ ] ✅ APPROVA BOZZA — spunta per pubblicare in wiki/ai
```

**Azione utente**: apre la bozza in Obsidian, legge e corregge se necessario, spunta la sentinel, salva.

**Azione watcher**: rileva `on_modified` in `drafts/`, lancia `approve_draft_auto.py <path>`.

### Idempotenza

Ogni script aggiunge un marker HTML invisibile in Obsidian per evitare doppi trigger:

- `process_proposal.py` appende `<!-- processed: YYYY-MM-DD HH:MM -->` in fondo alla proposal
- `approve_draft_auto.py` controlla `<!-- published` prima di agire

Se il marker è già presente, lo script esce immediatamente senza fare nulla.

### Debounce

Obsidian salva automaticamente ogni ~2 secondi. Il watcher deve ignorare trigger multipli sullo stesso file.

Implementazione: dizionario `last_triggered: dict[str, float]` a livello di modulo nel watcher. Se `time.time() - last_triggered.get(path, 0) < 3.0` → skip.

---

## Flusso Completo Target

```
[REMOTO o LOCALE]
Utente copia nuovo-paper.md in /raw/ai/
          │
          ▼
wiki_watcher.py  (on_created in /raw/ai)
          │
          ├─► ingest_assistant.py "nuovo-paper.md"
          │       └─► pending_ingests/proposal_nuovo-paper.md
          │               ┌──────────────────────────────────────────┐
          │               │ - [ ] ✅ AVVIA INGEST  ← GATE 1          │
          │               │ - [ ] [[entity-one]] → Nuova pagina       │
          │               │ - [ ] [[EntityTwo]]  → Già esistente      │
          │               │ - [ ] [[entity-tre]] → Nuova pagina       │
          │               └──────────────────────────────────────────┘
          └─► confidence_manager.py  (aggiorna health report)

[UTENTE IN OBSIDIAN]
Apre proposal, spunta [x] entity-one, [x] EntityTwo, [x] ✅ AVVIA INGEST
Salva (Ctrl+S o auto-save Obsidian)
          │
          ▼
wiki_watcher.py  (on_modified in /pending_ingests)
          │
          ▼
process_proposal.py "proposal_nuovo-paper.md"
  - controlla sentinel [x] e assenza <!-- processed -->
  - estrae: entity-one, EntityTwo  (esclude riga sentinel)
  - per ognuna: chiama generate_draft_for_entity() → genera bozza
  - appende <!-- processed: 2026-04-22 14:35 --> alla proposal
          │
          ├─► drafts/draft_entity-one.md
          │       ┌───────────────────────────────────────────────┐
          │       │ # entity-one                                  │
          │       │ **Summary**: ...                              │
          │       │ ...corpo pagina...                            │
          │       │ ---                                           │
          │       │ *Bozza generata automaticamente...*           │
          │       │ - [ ] ✅ APPROVA BOZZA  ← GATE 2             │
          │       └───────────────────────────────────────────────┘
          └─► drafts/draft_entitytwo.md  (bozza aggiornamento)

[UTENTE IN OBSIDIAN]
Legge draft_entity-one.md, corregge se necessario
Spunta [x] ✅ APPROVA BOZZA, salva
          │
          ▼
wiki_watcher.py  (on_modified in /drafts, non recursive)
          │
          ▼
approve_draft_auto.py "draft_entity-one.md"
  - controlla sentinel [x] e assenza <!-- published -->
  - estrae slug da H1 → "entity-one"
  - rimuove blocco sentinel e commento HTML metadata
  - scrive → wiki/ai/entity-one.md
  - sposta bozza → drafts/approved/draft_entity-one.md
  - lancia confidence_manager.py
          │
          ▼
wiki/ai/entity-one.md  ✓  (pagina pubblicata)
health-report.md       ✓  (aggiornato)
```

---

## Script da Creare

### `process_proposal.py`

**Ruolo**: Nodo di connessione Gate 1 → llm_bridge. Legge le checkbox spuntate nella proposal e avvia la generazione LLM solo per quelle entità.

**Input**: path assoluto di una proposal (`pending_ingests/proposal_*.md`) passato come `sys.argv[1]`

**Logica passo per passo**:

1. Legge il file
2. Controlla presenza di `- [x]` nella riga sentinel (`AVVIA INGEST`)
3. Controlla assenza di `<!-- processed` nel contenuto → se presente, exit silenzioso
4. Estrae le entità approvate con regex `^- \[x\] \[\[(.+?)\]\]` sulle righe che **non** contengono `AVVIA INGEST`
5. Deriva il nome del candidates JSON dal nome della proposal: `proposal_Agentmemory.md` → `candidates_Agentmemory.json`
6. Carica il candidates JSON, carica il testo sorgente completo da `/raw/ai/`
7. Per ogni entità estratta: chiama `generate_draft_for_entity(entity, candidates, source_text, source_file, cfg)`
8. Appende `<!-- processed: YYYY-MM-DD HH:MM -->` al file proposal

**Note implementative**:

- Il marker va appeso (non sostituito) per non alterare le checkbox già spuntate
- Se un'entità non è trovata nei candidates JSON (entità aggiunta manualmente dall'utente nella proposal), costruire un item sintetico `{"entity": name, "type": "Concept/Term", "count": 0}`
- `generate_draft_for_entity()` è una funzione da estrarre da `llm_bridge.py` — vedi sezione modifiche

**Usage**:
```bash
python process_proposal.py "D:/obsidian_git/projects/wiki-v2-implementation/pending_ingests/proposal_Agentmemory.md"
```

---

### `approve_draft_auto.py`

**Ruolo**: Gate 2 automatico. Pubblica una bozza approvata in `wiki/ai/` senza richiedere interazione da terminale. Equivale a `approve_drafts.py [a]` ma guidato dalla sentinel checkbox invece dell'input utente.

**Input**: path assoluto di una bozza (`drafts/draft_*.md`) passato come `sys.argv[1]`

**Logica passo per passo**:

1. Legge il file
2. Controlla presenza di `- [x]` nella riga sentinel (`APPROVA BOZZA`)
3. Controlla assenza di `<!-- published` nel contenuto → se presente, exit silenzioso
4. Riusa `extract_page_slug()` importato da `approve_drafts.py` per derivare lo slug dall'H1
5. Rimuove dal contenuto:
   - Blocco commento HTML metadata: `re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)`
   - Blocco sentinel finale: riga `---`, riga italics bozza, riga `- [x] ✅ APPROVA BOZZA`
6. Scrive la pagina pulita in `wiki/ai/<slug>.md`
7. Crea `drafts/approved/` se non esiste (`mkdir(exist_ok=True)`)
8. Sposta la bozza in `drafts/approved/` con `shutil.move()`
9. Lancia `confidence_manager.py` via `subprocess.run([sys.executable, str(MANAGER_SCRIPT)], check=True)`

**Riusa da `approve_drafts.py`**: `extract_page_slug()`, `extract_draft_metadata()`

**Usage**:
```bash
python approve_draft_auto.py "D:/obsidian_git/projects/wiki-v2-implementation/drafts/draft_entity-one.md"
```

---

## Script da Modificare

### 1. `ingest_assistant.py`

**Funzione**: `create_ingest_proposal()`

**Modifica**: aggiungere la riga sentinel subito dopo `f.write("## Entità Rilevate\n\n")` e prima del blocco `if not entities`.

**Posizione esatta nel codice attuale**:
```python
        f.write("## Entità Rilevate\n\n")

        if not entities:
            f.write("Nessuna entità rilevante rilevata dopo il filtraggio.\n")
        else:
            for item in entities:
```

**Diventa**:
```python
        f.write("## Entità Rilevate\n\n")
        f.write("- [ ] ✅ AVVIA INGEST — spunta dopo aver selezionato le entità sopra\n\n")

        if not entities:
            f.write("Nessuna entità rilevante rilevata dopo il filtraggio.\n")
        else:
            for item in entities:
```

---

### 2. `llm_bridge.py`

**Modifica A**: estrarre la logica di generazione per singola entità in una funzione pubblica importabile:

```python
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
```

Questa funzione contiene la logica attualmente inline nel loop `for item in candidates:` di `process_candidates_file()`.

**Modifica B**: aggiungere il blocco sentinel in fondo ad ogni bozza scritta. Nel punto in cui il file bozza viene scritto:

```python
        # Blocco sentinel Gate 2
        sentinel_block = (
            "\n\n---\n"
            "*Bozza generata automaticamente — richiede revisione prima della pubblicazione.*\n\n"
            "- [ ] ✅ APPROVA BOZZA — spunta per pubblicare in wiki/ai\n"
        )
        draft_path.write_text(metadata_comment + "\n\n" + draft_content + sentinel_block, encoding="utf-8")
```

---

### 3. `wiki_watcher.py`

**Modifica**: aggiungere due nuovi handler e i relativi `observer.schedule()`.

**Debounce condiviso** (a livello di modulo, prima delle classi):
```python
import time
_last_triggered: dict[str, float] = {}
_DEBOUNCE_SECONDS = 3.0

def _debounce_check(path: str) -> bool:
    """Ritorna True se il path può essere processato (debounce ok)."""
    now = time.time()
    if now - _last_triggered.get(path, 0) < _DEBOUNCE_SECONDS:
        return False
    _last_triggered[path] = now
    return True
```

**Nuovo `ProposalEventHandler`**:
```python
class ProposalEventHandler(FileSystemEventHandler):
    """Monitora pending_ingests/ — triggera process_proposal.py su on_modified."""

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            if _debounce_check(event.src_path):
                self._handle_modified(event.src_path)

    def _handle_modified(self, file_path: str) -> None:
        name = Path(file_path).name
        print(f"[~] Proposal modificata: {name}")
        print(f"    Controllo sentinel AVVIA INGEST...")
        try:
            subprocess.run(
                [sys.executable, str(PROCESS_PROPOSAL_SCRIPT), file_path],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[!] Errore in process_proposal: {e}")
```

**Nuovo `DraftEventHandler`**:
```python
class DraftEventHandler(FileSystemEventHandler):
    """Monitora drafts/ (non recursive) — triggera approve_draft_auto.py su on_modified."""

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            # Ignora file in approved/ (recursive=False lo gestisce, ma doppio check)
            if "approved" not in event.src_path and _debounce_check(event.src_path):
                self._handle_modified(event.src_path)

    def _handle_modified(self, file_path: str) -> None:
        name = Path(file_path).name
        print(f"[~] Bozza modificata: {name}")
        print(f"    Controllo sentinel APPROVA BOZZA...")
        try:
            subprocess.run(
                [sys.executable, str(APPROVE_DRAFT_SCRIPT), file_path],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[!] Errore in approve_draft_auto: {e}")
```

**Costanti script** (aggiungere accanto a `ASSISTANT_SCRIPT` e `MANAGER_SCRIPT`):
```python
PROCESS_PROPOSAL_SCRIPT = _HERE / "process_proposal.py"
APPROVE_DRAFT_SCRIPT    = _HERE / "approve_draft_auto.py"
```

**Blocco `__main__`** — aggiungere i due schedule dopo quello esistente:
```python
proposals_path = Path(cfg["paths"]["proposals_dir"])
drafts_path    = Path(cfg["paths"]["drafts_dir"])

observer.schedule(WikiEventHandler(),     str(raw_path),       recursive=False)
observer.schedule(ProposalEventHandler(), str(proposals_path), recursive=False)
observer.schedule(DraftEventHandler(),    str(drafts_path),    recursive=False)
```

**Aggiornare il banner di avvio**:
```
1. Aggiungi un .md in /raw/ai        -> Ingest Assistant (solo quel file)
2. Spunta sentinel in proposal       -> Process Proposal (entità selezionate)
3. Spunta sentinel in bozza          -> Approve Draft (pubblica in wiki/ai)
4. Dopo ogni publish                 -> Confidence Manager si aggiorna
```

---

### 4. `config.toml`

**Modifica**: aggiungere nella sezione `[paths]` (dopo `candidates_dir`):

```toml
drafts_dir = "D:/obsidian_git/projects/wiki-v2-implementation/drafts"
```

---

## Tabella Riepilogativa — Tutti gli Script

| Script | Stato | Trigger | Input | Output |
|:---|:---:|:---|:---|:---|
| `extract_candidates.py` | invariato | manuale | file `.md` in `/raw/ai` | `candidates/*.json` |
| `ingest_assistant.py` | **modifica** | watcher `on_created` in `/raw/ai` | file `.md` | `pending_ingests/proposal_*.md` con sentinel |
| `process_proposal.py` | **nuovo** | watcher `on_modified` in `/pending_ingests` | `proposal_*.md` con `[x]` sentinel | `drafts/draft_*.md` con sentinel |
| `llm_bridge.py` | **modifica** | chiamato da `process_proposal.py` | candidates JSON + entità selezionate | `drafts/draft_*.md` |
| `approve_draft_auto.py` | **nuovo** | watcher `on_modified` in `/drafts` | `draft_*.md` con `[x]` sentinel | `wiki/ai/*.md` |
| `approve_drafts.py` | invariato | manuale (terminale) | `drafts/*.md` | `wiki/ai/*.md` |
| `confidence_manager.py` | invariato | chiamato dopo ogni publish | `wiki/ai/` | frontmatter + `health-report.md` |
| `wiki_watcher.py` | **modifica** | avvio manuale / `run_watcher.bat` | — | orchestra tutto |
| `utils.py` | invariato | importato dagli script | — | funzioni condivise |
| `config.toml` | **modifica** | letto da tutti gli script | — | aggiunge `drafts_dir` |

---

## Checkpoint di Verifica (test end-to-end)

1. Avviare il watcher (`python wiki_watcher.py`)
2. Copiare un file `.md` in `/raw/ai/` → verificare che appaia `pending_ingests/proposal_*.md` con la riga `- [ ] ✅ AVVIA INGEST` in cima alla sezione entità
3. In Obsidian: aprire la proposal, spuntare 2-3 entità + la sentinel `✅ AVVIA INGEST`
4. Salvare → verificare che appaiano `drafts/draft_*.md` con sentinel `✅ APPROVA BOZZA` in fondo
5. In Obsidian: aprire una bozza, leggere il contenuto, spuntare `✅ APPROVA BOZZA`
6. Salvare → verificare che appaia la pagina in `wiki/ai/<slug>.md`
7. Verificare che `health-report.md` sia aggiornato con il nuovo score
8. **Test idempotenza**: spuntare di nuovo la sentinel già processata → nessun effetto, nessun errore

---

## Decisioni di Design

| Decisione | Motivazione |
|:---|:---|
| Debounce 3 secondi | Obsidian auto-save ogni ~2s; 3s evita doppi trigger senza ritardo percepibile |
| Marker HTML `<!-- processed -->` | Invisibile in Obsidian render, non altera le checkbox visibili all'utente |
| `approve_drafts.py` rimane invariato | Fallback terminale per uso manuale o in caso di problemi con il watcher |
| `approve_draft_auto.py` script separato | Non modifica comportamento esistente; più facile testare in isolamento |
| Entità "già esistenti" generano bozza di aggiornamento | Coerente con il comportamento attuale di `llm_bridge.py` |
| `recursive=False` su observer `/drafts` | Evita trigger su file in `drafts/approved/` |

---

## Fuori Scope

- Modifiche a `AGENTS.md` o al workflow OpenCode manuale
- Knowledge Graph (Fase 3 del roadmap)
- Retention/decay engine (Fase 4 del roadmap)
- Integrazione con `extract_candidates.py` nel watcher (già funzionante separatamente)
