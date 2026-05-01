# Roadmap: Evoluzione LLM Wiki v2
**Obiettivo**: Trasformare la Knowledge Base da un archivio statico di file Markdown a un sistema di memoria attivo, scalabile e auto-manutenuto.

---

## 🌟 Visione d'Insieme
L'obiettivo non è sostituire i file Markdown (che restano l'interfaccia di lettura e scrittura), ma aggiungere un "motore" invisibile che gestisce la qualità, la validità e la connessione dei dati. Passiamo da una gestione manuale a una **gestione assistita da agenti**.

---

## 🗺️ Fasi di Implementazione

### Fase 1: The Insight Layer (Analisi & Confidence)
*Obiettivo: Misurare la salute della wiki e introdurre la nozione di "affidabilità".*
- **Analizzatore di Confidenza**: Script Python per calcolare il punteggio di un concetto basandosi sulla frequenza di citazione e recenza.
- **Rilevatore di Fragilità**: Identificazione automatica di note "isolate" o affermazioni a basso supporto.
- **Visualizzazione**: Introduzione di metadata (YAML) nelle pagine per rendere i punteggi leggibili in Obsidian.

### Fase 2: The Automation Bridge (Hooks & Ingest)
*Obiettivo: Ridurre il carico di bookkeeping manuale.*
- **Semplificatore di Ingest**: Tool per l'estrazione automatica di entità (Persone, Progetti, Tool) dai file in `/raw`.
- **Session Compressor**: Workflow per distillare le conversazioni in "osservazioni" da archiviare.
- **Auto-Linking**: Suggerimenti automatici di link tra pagine basati sulla similarità semantica.
- **Human-in-the-Loop (HITL)**: a ogni modifica automatica seguirà una fase di "bozza" per approvazione manuale dell'utente, prevenendo la propagazione di allucinazioni.

### Fase 3: The Structural Leap (Graph & Hybrid Search)
*Obiettivo: Gestire la crescita della wiki oltre le 200 pagine.*
- **Knowledge Graph (Neo4j/FalkorDB)**: Implementazione di relazioni tipizzate (es. `X` *causa* `Y`, `A` *supersede* `B`).
- **File Watcher**: Implementazione di un monitor in tempo reale per sincronizzare istantaneamente le modifiche dei file Markdown con il database a grafo.
- **Ricerca Ibrida**: Integrazione di ricerca per parole chiave (BM25) e ricerca vettoriale (Semantic search).
- **Graph Traversal**: Capacità di rispondere a domande complesse analizzando i collegamenti del grafo.

### Fase 4: The Living Organism (Lifecycle & Decay)
*Obiettivo: Implementare l'intelligenza della memoria a lungo termine.*
- **Retention Engine**: Implementazione della curva di dimenticanza di Ebbinghaus (deprioritizzazione dei dati obsoleti).
- **Consolidation Pipeline**: Passaggio automatico delle note da *Working Memory* $\rightarrow$ *Semantic* $\rightarrow$ *Procedural*.
- **Self-Healing**: Agente di linting che ripara automaticamente link rotti e risolve contraddizioni.

---

## 🛠️ Stack Tecnologica

| Componente | Strumento | Perché? | Interazione Utente |
| :--- | :--- | :--- | :--- |
| **Interfaccia** | Obsidian | Standard per markdown e visualizzazione grafo locale. | Alta (Lettura/Scrittura) |
| **Logica/Script** | Python 3.x | Linguaggio standard per AI e manipolazione file. | Bassa (Esecuzione script) |
| **Graph DB** | Neo4j / FalkorDB | Gestione di relazioni complesse non lineari. | Minima (Configurazione guidata) |
| **Vector DB** | Qdrant / Weaviate | Ricerca semantica ultra-rapida. | Minima (Configurazione guidata) |
| **Orchestrazione** | LangGraph | Per creare agenti che "ragionano" sui processi di manutenzione. | Bassa (Definizione regole) |

---

## 🤝 Metodo di Lavoro (Il Patto)

1. **Progettazione (Gemma)**: Io propongo il micro-step e scrivo il codice.
2. **Esecuzione (Utente)**: Tu esegui lo script o installi lo strumento seguendo la mia guida passo-passo.
3. **Validazione (Insieme)**: Verifichiamo che il risultato sia corretto e utile.
4. **Consolidamento**: Una volta approvato, lo step diventa parte della "baseline" e passiamo al successivo.

**Slogan**: *"Piccoli passi, grandi salti."* Non implementeremo nulla che non sia comprensibile e testabile.

---
**Stato**: 🟢 Pianificato | **Versione**: 1.0 | **Data**: 2026-04-22
