# @tfsc/refine — normalization engine (Python + TypeScript)

One engine, two implementations, one contract: `vectors.json`.

- `python/tfsc_refine` — reference implementation used by the CRM API (authoritative on save).
- `ts/src/index.ts` — identical port used in the browser (Web Worker preview).
- `vectors.json` — shared test vectors (283 cases). Both test suites load it.
- `python/tests/test_required_vectors.py` — the owner's mandatory vectors from master command §10.1 (hand-written).

Change workflow: edit both implementations, add inputs to `tools/build_vectors.py`, run it, **review the
diff of vectors.json by eye**, then run `pytest packages/refine/python` and `npm test` in `ts/`.

Measured (2026-10-20, cloud container): 1,000 rows refined in ~76 ms (TS, Node 22) and ~113 ms (Python 3.13).
