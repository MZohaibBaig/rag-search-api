"""Retrieval eval: recall@5, MRR and abstain rate over the demo document.

Run from the repo root:  python -m tests.eval.run_eval [--json] [--questions]

Ingests the demo document through the real pipeline (app.rag.ingest_text) into a throwaway
database created next to DATABASE_URL's, runs every question through app.rag, and drops it.
No LLM is called. Needs a Postgres with pgvector and a role allowed to CREATE DATABASE.
"""
import argparse
import json
import re
from pathlib import Path

from tests.eval.questions import QUESTIONS
from tests.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[2]
DEMO_DOC = ROOT / "app" / "demo_data" / "brindlemoor-lighthouse.txt"
EXAMPLE_1 = "Who designed Brindlemoor Lighthouse, and why was it built?"
EXAMPLE_1_PHRASE = "Ottoline Varga"
K = 5
DEEP_K = 50


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


def search(db, doc_id: int, question: str, k: int) -> dict:
    """Adapter over app.rag: {'chunks': [(text, similarity)], 'abstain': bool}."""
    from app import rag
    if hasattr(rag, "retrieve"):
        r = rag.retrieve(db, doc_id, question, k=k)
        return {"chunks": [(h.chunk.chunk_text, 1 - h.distance) for h in r.hits], "abstain": r.abstain}
    pairs = rag.retrieve_chunks(db, doc_id, question, k=k)  # pre-hybrid API: nothing ever abstains
    return {"chunks": [(c.chunk_text, 1 - d) for c, d in pairs], "abstain": False}


def evaluate(db, doc_id: int) -> dict:
    rows = []
    for q in QUESTIONS:
        res = search(db, doc_id, q.question, 10)  # RRF/cosine ordering is prefix-stable in k
        texts = [_norm(t) for t, _ in res["chunks"]]
        top5 = texts[:K]
        row = {"kind": q.kind, "question": q.question, "abstain": res["abstain"],
               "top_similarity": round(res["chunks"][0][1], 4) if res["chunks"] else None,
               "best_similarity": round(max((s for _, s in res["chunks"]), default=0), 4)}
        if q.answerable:
            found = [any(_norm(p) in t for t in top5) for p in q.expected]
            found3 = [any(_norm(p) in t for t in texts[:3]) for p in q.expected]
            rank = next((i + 1 for i, t in enumerate(texts) if any(_norm(p) in t for p in q.expected)), None)
            row.update(recall=sum(found) / len(found), recall3=sum(found3) / len(found3),
                       rr=1 / rank if rank else 0.0, rank=rank,
                       missing=[p for p, f in zip(q.expected, found) if not f])
        rows.append(row)

    ans = [r for r in rows if "recall" in r]
    unans = [r for r in rows if "recall" not in r]
    by_kind = {}
    for r in ans:
        by_kind.setdefault(r["kind"], []).append(r["recall"])

    deep = search(db, doc_id, EXAMPLE_1, DEEP_K)
    pos = next((i + 1 for i, (t, _) in enumerate(deep["chunks"]) if EXAMPLE_1_PHRASE.lower() in t.lower()), None)
    return {
        "recall_at_5": round(sum(r["recall"] for r in ans) / len(ans), 4),
        "recall_at_3": round(sum(r["recall3"] for r in ans) / len(ans), 4),
        "mrr": round(sum(r["rr"] for r in ans) / len(ans), 4),
        "abstain_rate": round(sum(r["abstain"] for r in unans) / len(unans), 4),
        "abstain_by_kind": {k: f"{sum(r['abstain'] for r in unans if r['kind'] == k)}/"
                               f"{sum(r['kind'] == k for r in unans)}" for k in sorted({r["kind"] for r in unans})},
        "calibration": {
            "max_offtopic_similarity": max(r["best_similarity"] for r in unans if r["kind"] == "offtopic"),
            "min_answerable_similarity": min(r["best_similarity"] for r in ans),
            "near_domain_similarities": [r["best_similarity"] for r in unans if r["kind"] == "near-domain"],
        },
        "false_abstain_rate": round(sum(r["abstain"] for r in ans) / len(ans), 4),
        "recall_by_kind": {k: round(sum(v) / len(v), 4) for k, v in by_kind.items()},
        "n_answerable": len(ans), "n_unanswerable": len(unans),
        "example1_ottoline_rank": pos,
        "example1_ottoline_similarity": round(deep["chunks"][pos - 1][1], 4) if pos else None,
        "rows": rows,
    }


def run() -> dict:
    with scratch_database("rag_eval"):
        from app.database import SessionLocal, engine, init_db
        from app.models import User
        from app.rag import ingest_text
        init_db()
        db = SessionLocal()
        try:
            user = User(username="eval", email="eval@localhost.invalid", hashed_password="x")
            db.add(user)
            db.commit()
            doc = ingest_text(db, user.id, DEMO_DOC.name, DEMO_DOC.read_text(encoding="utf-8"))
            n_chunks = len(doc.chunks)
            result = evaluate(db, doc.id)
            result["n_chunks"] = n_chunks
            return result
        finally:
            db.close()
            engine.dispose()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print only the JSON result")
    ap.add_argument("--questions", action="store_true", help="also print per-question rows")
    args = ap.parse_args()
    r = run()
    if args.json:
        print(json.dumps(r))
        return
    print(f"chunks in doc: {r['n_chunks']}   answerable: {r['n_answerable']}   unanswerable: {r['n_unanswerable']}")
    print(f"recall@5            {r['recall_at_5']:.3f}   by kind: {r['recall_by_kind']}")
    print(f"recall@3            {r['recall_at_3']:.3f}   (stricter: the doc is small, so @5 is forgiving)")
    print(f"MRR                 {r['mrr']:.3f}")
    print(f"abstain (unanswerable) {r['abstain_rate']:.2f} {r['abstain_by_kind']}   "
          f"false abstain (answerable) {r['false_abstain_rate']:.2f}")
    print(f"calibration         {r['calibration']}")
    print(f"example 1 '{EXAMPLE_1_PHRASE}' chunk: rank {r['example1_ottoline_rank']}, "
          f"similarity {r['example1_ottoline_similarity']}")
    if args.questions:
        for row in r["rows"]:
            tail = (f"recall={row['recall']:.2f} rank={row['rank']}" if "recall" in row
                    else f"abstain={row['abstain']}")
            print(f"  [{row['kind']:12}] top_sim={row['top_similarity']} {tail}  {row['question']}")


if __name__ == "__main__":
    main()
