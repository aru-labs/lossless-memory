# Quickstart

Five minutes, from a clean checkout to a real search result.

## 1. Install

```
git clone https://github.com/aru-labs/lossless-memory.git
cd lossless-memory
pip install -e .
```

## 2. Configure

Copy the example config and point it at this repo's sample data:

```
cp config.example.json config.json
```

Edit `config.json` so it reads:

```json
{
  "user_name": "Sam",
  "ai_name": "Nova",
  "data_dir": "./logs",
  "raw_log_dir": "./examples",
  "ingest_format": "plain"
}
```

`raw_log_dir` is where `ingest` looks for `*.jsonl` files to convert;
`examples/sample_log.jsonl` (a made-up, three-day conversation) is
already there. `data_dir` is where the converted logs and the search
indexes will be written -- nothing outside that directory is ever
touched.

## 3. Ingest and index

```
python -m lossless_memory.ingest --format=plain
python -m lossless_memory.index_exact
```

The first command converts `examples/sample_log.jsonl` into daily
files under `logs/main/`. The second builds the full-text search
index from those files.

(Building the semantic index -- `python -m lossless_memory.index_vector build`
-- downloads a small sentence-transformers model on first use and is
optional for this quickstart; `recall` works from the exact index
alone.)

## 4. Recall

```
python -m lossless_memory.recall "2026-09-02 budget"
```

This is a date-scoped query: it returns everything from 2026-09-02
that mentions "budget", verbatim, with a timestamp on every line --
not a summary. Try a plain keyword search too:

```
python -m lossless_memory.recall "printing invoice"
```

That's the whole loop: **ingest** turns raw conversation logs into the
lossless record format, **index_exact** / **index_vector** make them
searchable, and **recall** is the one door you search through.
