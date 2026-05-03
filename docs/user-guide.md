# Guida d'uso — Wiki Pipeline

Questa guida risponde alla domanda pratica: **"voglio fare X, cosa faccio?"**

Per i dettagli tecnici interni vedi [README.md](../README.md) e [workflow.md](workflow.md).

---

## Avvio rapido

Prima di qualsiasi operazione, il watcher deve essere attivo. È il motore che
osserva le cartelle e triggera automaticamente i passi giusti.

**Windows — un doppio click:**
```
pipeline/run_watcher.bat
```

**Terminale:**
```bash
cd d:/obsidian_git
.venv/Scripts/activate
python pipeline/wiki_watcher.py
```

Lascia aperto il terminale. Il watcher girerà finché non lo chiudi.

---

## Flusso normale — ingestire una nuova fonte

### 1. Copia il file sorgente

Metti il tuo `.md` (paper, articolo, appunti) in:
```
raw/ai/nome-del-file.md
```

Se il watcher è attivo, la pipeline parte da sola in pochi secondi.
Se non è attivo, esegui manualmente:
```bash
python pipeline/extract_candidates.py
python pipeline/ingest_assistant.py
```

### 2. Approva le entità (Gate 1 — Obsidian)

Apri in Obsidian il file appena creato in:
```
pipeline/pending_ingests/proposal_nome-del-file.md
```

Vedrai un elenco di entità rilevate (concetti, tool, nomi). Per ognuna:
- **spunta** quelle che vuoi inserire nel wiki
- **lascia vuote** quelle irrilevanti o già ben coperte

Quando hai finito, spunta il checkbox in cima:
```
- [x] ✅ AVVIA INGEST
```

Salva il file. Il watcher rileva il salvataggio e chiama l'LLM per le entità selezionate.

### 3. Approva le bozze (Gate 2 — Obsidian)

Per ogni entità approvata, appare una bozza in:
```
pipeline/drafts/draft_nome-entità.md
```

Apri ogni bozza in Obsidian, leggila. Se va bene, spunta:
```
- [x] ✅ APPROVA BOZZA
```

Salva. Il watcher pubblica automaticamente la pagina pulita in `wiki/ai/`.

> Le bozze approvate vengono spostate in `drafts/approved/` per tracciabilità.

---

## Operazioni di manutenzione

### Vedere lo stato di salute del wiki

```bash
python pipeline/wiki_maintenance.py
```

Genera `wiki/ai/maintenance-report.md` con:
- pagine orfane (nessun link in entrata)
- link rotti (puntano a pagine inesistenti)
- duplicati sospetti
- sezioni mancanti

Per un'analisi più approfondita con valutazione LLM:
```bash
python pipeline/wiki_maintenance.py --full
```

Per analizzare una singola pagina:
```bash
python pipeline/wiki_maintenance.py --page alphago
```

### Applicare le correzioni del maintenance report

Apri `wiki/ai/maintenance-report.md` in Obsidian.
Spunta **solo** le azioni che vuoi applicare (rinomina link, aggiungi related pages, ecc.).

Per vedere in anteprima cosa cambierebbe (dry-run):
```bash
python pipeline/apply_maintenance.py
```

Per applicare effettivamente le modifiche spuntate:
```bash
python pipeline/apply_maintenance.py --apply
```

### Aggiornare i confidence score

I confidence score vengono aggiornati automaticamente dopo ogni bozza approvata.
Per forzare un ricalcolo manuale su tutto il wiki:
```bash
python pipeline/confidence_manager.py
```

Genera anche `wiki/ai/health-report.md` con le pagine sotto soglia.

---

## Chiedere qualcosa al wiki (chat)

Apri `chat/inbox.md` in Obsidian e aggiungi una voce in fondo:

```markdown
---

**D:** La tua domanda qui

**A:** *(in attesa...)*
```

Poi esegui:
```bash
python pipeline/chat_responder.py
```

L'LLM cercherà le pagine wiki più rilevanti, le userà come contesto e scriverà
la risposta direttamente sotto il `**A:**`.

---

## Revisione manuale da terminale (alternativa a Obsidian)

Se preferisci approvare le bozze da terminale invece che da Obsidian:
```bash
python pipeline/approve_drafts.py
```

Mostra ogni bozza interattivamente: `[a]pprova / [s]kip / [q]uit`.

---

## Struttura delle cartelle — dove trovare cosa

| Cartella / File | Cosa contiene |
|:---|:---|
| `raw/ai/` | Fonti originali — **non modificare mai** |
| `pipeline/pending_ingests/` | Proposal da revisionare (Gate 1) |
| `pipeline/drafts/` | Bozze da approvare (Gate 2) |
| `pipeline/drafts/approved/` | Bozze già pubblicate (archivio) |
| `pipeline/candidates/` | JSON intermedi dell'estrazione entità |
| `wiki/ai/` | Pagine wiki pubblicate |
| `wiki/ai/health-report.md` | Pagine con confidence score basso |
| `wiki/ai/maintenance-report.md` | Problemi strutturali del wiki |
| `wiki/log.md` | Log append-only di tutte le operazioni |
| `pipeline/config.toml` | Configurazione centralizzata |

---

## Configurazione rapida (`config.toml`)

Le impostazioni che tocchi più spesso:

```toml
[llm]
model = "gemma4:31b-cloud"     # cambia il modello LLM qui
base_url = "http://..."        # endpoint API

[confidence]
threshold = 1.5                # sotto questa soglia: pagina "fragile"

[extraction]
min_count_concept = 2          # occorrenze minime per includere un termine
```

---

## Problemi comuni

**Il watcher non parte / si chiude subito**
→ Controlla che il venv sia attivo e che `config.toml` abbia i path corretti.

**Il watcher è attivo ma non genera la proposal**
→ Controlla che il file `.md` sia finito in `raw/ai/` e non in una sottocartella.
→ Controlla il terminale: potrebbe esserci un errore LLM.

**La bozza è stata approvata ma non appare in `wiki/ai/`**
→ Verifica che il checkbox `✅ APPROVA BOZZA` sia effettivamente `[x]` (non `[ ]`).
→ Il file va salvato dopo la spunta perché Obsidian scriva su disco.

**L'LLM restituisce errore**
→ Controlla che l'endpoint in `config.toml` sia raggiungibile.
→ Verifica che la variabile d'ambiente con l'API key sia impostata.
