# Knowledge Engine

Retrieval-augmented question answering over World of Warcraft knowledge.
Answers carry citations and a confidence level, and the engine refuses to
answer rather than guess when the evidence is thin.

## Source licensing — read this first

Your brief said "where permitted". Permission was checked per source against
robots.txt and published terms, and the result is **enforced in code**, not
documented in a comment. `app/knowledge/policy.py` is the single authority.

| Source | Tier | Basis |
|---|---|---|
| **Blizzard Game Data API** | `FULL_TEXT` | First-party API, our own credentials. Authoritative. |
| **Warcraft Wiki** | `FULL_TEXT` | CC BY-SA 4.0. robots.txt signals `search=yes, ai-train=no, use=reference` — RAG grounding is reference use; training is not permitted. |
| **Raider.IO** | `STRUCTURED_FACTS` | ToU bans compiling a database from the site but excepts the public API. API only, never scraped. |
| **Icy Veins** | `METADATA_ONLY` | Crawlable, but no content licence. Title + URL indexed; guide prose never stored. |
| **Method** | `METADATA_ONLY` | Same as Icy Veins. |
| **Petopia** | `METADATA_ONLY` | No robots.txt, no licence, no API. Pet facts sourced from Blizzard instead. |
| **Wowhead** | `DISABLED` | robots.txt issues `Disallow: /` to `ClaudeBot`, `GPTBot`, `CCBot`, `anthropic-ai`, `Scrapy` and others — an explicit refusal of AI ingestion. |

Enforcement is at the type boundary: `KnowledgeDocument.__post_init__` drops
body text from metadata-only sources and raises `PolicyViolationError` for
disabled ones. A connector cannot opt itself out, however it is written.

Wowhead's coverage is replaced by the Blizzard Game Data API, which is both
authoritative and licensed for our use.

> **Commercial deployment:** Raider.IO requires prior written permission for
> commercial use. Obtain it before setting `KNOWLEDGE_COMMERCIAL_MODE=true`.

## Architecture

```
question
   ↓
infer filters ──── game_version is a hard filter (a Classic answer to a
   ↓                retail player is confidently, plausibly wrong)
hybrid search ──── pgvector cosine ∥ Postgres full-text, fused with RRF
   ↓
assess confidence ─ computed from evidence, BEFORE generation
   ↓
   ├── insufficient → refuse, no model call, no hallucination
   ↓
build context ──── numbered, token-budgeted passages
   ↓
generate ───────── strict grounding prompt, temperature 0.2
   ↓
verify citations ─ markers not in the supplied context are stripped
   ↓
answer + citations + confidence
```

### Why hybrid retrieval

Dense embeddings handle paraphrase ("how do I beat the last boss of
Karazhan") but are weak on exact proper nouns — and WoW questions are
saturated with them ("Thunderfury, Blessed Blade of the Windseeker"). Lexical
search nails those. Reciprocal Rank Fusion combines the two ranked lists;
RRF is used instead of a weighted score sum because the two scores are not on
comparable scales, and rank-only fusion stays valid when the embedding model
changes.

### Why confidence is computed before generation

Model self-reported confidence is poorly calibrated. This engine derives
confidence from what retrieval actually found:

| Signal | Weight | Meaning |
|---|---|---|
| `top_relevance` | 0.45 | Best absolute match quality (cosine / lexical overlap) |
| `corroboration` | 0.25 | Distinct **supporting** documents, capped at 3 |
| `authority` | 0.20 | Whether Blizzard's own API backs the answer |
| `agreement` | 0.10 | Score spread across top hits |

A hard floor applies: if `top_relevance` falls below `_MIN_EVIDENCE_SCORE`,
the level is `INSUFFICIENT` regardless of the weighted score, and **the model
is never called**.

> **Design note.** An earlier revision derived confidence from the fused RRF
> score. That was wrong, and an end-to-end test caught it: RRF encodes
> *position*, not quality, so the best of a uniformly terrible candidate set
> still scores maximally at rank 1 — making nonsense questions look well
> supported. Confidence now reads absolute similarity. See
> `test_unanswerable_question_is_refused_without_calling_the_model`.

## Layout

| File | Role |
|---|---|
| `policy.py` | Source licence registry — the compliance boundary |
| `documents.py` | `KnowledgeDocument`, `DocumentChunk`, policy enforcement |
| `chunking.py` | Heading-aware splitting with sentence-boundary overlap |
| `embeddings.py` | Provider abstraction + deterministic offline embedder |
| `vector_store.py` | pgvector persistence and hybrid search |
| `confidence.py` | Citation rendering and confidence estimation |
| `rag.py` | The answering pipeline |
| `ingestion.py` | Streaming, incremental, auditable indexing |
| `connectors/` | One module per source |

## Adding a source

1. Register it in `policy.py` with an evidence-backed tier. Unregistered
   sources are refused, not defaulted.
2. Subclass `BaseConnector`, set `source`, implement `fetch()` as an async
   generator yielding `KnowledgeDocument`s.
3. Decorate with `@register_connector`.

Nothing else changes — chunking, embedding, storage and retrieval are all
source-agnostic.

## Configuration

```bash
EMBEDDING_PROVIDER=openai          # or "hashing" for offline/CI
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536          # must match the pgvector column
KNOWLEDGE_DEFAULT_TOP_K=8
KNOWLEDGE_COMMERCIAL_MODE=false
```

`hashing` is a real deterministic embedder using the signed hashing trick, not
a stub: identical text yields identical vectors and lexically similar text
yields closer vectors. It makes the full retrieval stack testable in CI with
no network access or API spend. It is not semantically meaningful — use
`openai` in production.

Changing `EMBEDDING_DIMENSIONS` requires a migration and a full re-embed. The
`embedding_model` column stored per document makes the mismatch detectable and
triggers automatic re-embedding on the next ingestion run.

## Database

Migration `0003_knowledge_engine` creates the `vector` extension, the tables,
and two indexes:

- **HNSW** (`m=16, ef_construction=64`) over cosine distance — better
  recall/latency than IVFFlat and no retraining as rows are added.
- **GIN** over a *generated* `tsvector` column. Generated, so no code path can
  write chunk text and forget to refresh the index. Title is weighted `A`
  above body `B`, so a proper-noun title match outranks an incidental mention.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/knowledge/ask` | Grounded answer with citations + confidence |
| `POST /api/v1/knowledge/search` | Retrieval only — no model call, much cheaper |
| `GET /api/v1/knowledge/stats` | Index contents and per-source policy |
| `POST /api/v1/knowledge/ingest` | Run ingestion (admin; 403 for disabled sources) |

Clients should check `refused` and `confidence.level` before presenting an
answer as reliable.

## Operations

Ingestion is **incremental**: documents are hashed, and unchanged ones skip
both re-embedding and re-writing, so a re-run is cheap. It **streams** rather
than buffering a source, **commits in batches** so a crash loses at most 32
documents, and records an auditable `IngestionRun` row per attempt — which
doubles as the compliance trail, since it captures the tier each run operated
under.

Sources are ingested sequentially, not concurrently: each has its own
politeness budget, and parallelism would multiply load on community fan sites
for no real gain on an offline job.

```bash
# Smoke-test a connector without writing or spending on embeddings
curl -X POST /api/v1/knowledge/ingest \
  -d '{"source": "warcraft_wiki", "dry_run": true, "limit": 10}'
```

## Testing

```bash
pytest tests/test_knowledge.py           # units: policy, chunking, confidence
pytest tests/test_knowledge_pipeline.py  # end-to-end via in-memory store
```

Both run fully offline. The pipeline suite exercises the real chunker,
embedder, fusion, confidence model and citation verifier, substituting only
PostgreSQL and the LLM — so component wiring is covered, not just units.
