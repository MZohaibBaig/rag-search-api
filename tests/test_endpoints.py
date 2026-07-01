"""
End-to-end API test script for the RAG Search API.
Usage: python tests/test_endpoints.py
"""

import sys
import httpx

BASE_URL = "http://127.0.0.1:8000"

USERNAME = "testuser"
EMAIL = "test@example.com"
PASSWORD = "testpass123"

SAMPLE_DOC = (
    "Artificial Intelligence (AI) is the simulation of human intelligence in machines.\n"
    "Machine learning is a subset of AI that enables systems to learn from data.\n"
    "Deep learning uses neural networks with many layers to process complex patterns.\n"
    "Natural Language Processing (NLP) allows computers to understand human language.\n"
    "RAG (Retrieval-Augmented Generation) combines retrieval systems with generative AI."
)


def _pass(step: str, status: int, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"  PASS  [{status}] {step}{suffix}")


def _fail(step: str, status: int, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"  FAIL  [{status}] {step}{suffix}")


def run_tests() -> bool:
    passed = 0
    total = 7
    token: str | None = None
    document_id: int | None = None

    with httpx.Client(base_url=BASE_URL, timeout=60.0) as client:

        # Step 1: Register
        r = client.post(
            "/auth/register",
            json={"username": USERNAME, "email": EMAIL, "password": PASSWORD},
        )
        if r.status_code == 200:
            _pass("Register user", r.status_code, f"id={r.json()['id']}")
            passed += 1
        elif r.status_code == 400:
            _pass("Register user", r.status_code, "already exists — skipping, will login")
            passed += 1
        else:
            _fail("Register user", r.status_code, r.text)

        # Step 2: Login
        r = client.post(
            "/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        if r.status_code == 200:
            token = r.json()["access_token"]
            _pass("Login", r.status_code, "token received")
            passed += 1
        else:
            _fail("Login", r.status_code, r.text)

        if token is None:
            print("\n  Cannot continue — login failed, no token.")
            _summarize(passed, total)
            return False

        auth = {"Authorization": f"Bearer {token}"}

        # Step 3: GET /auth/me
        r = client.get("/auth/me", headers=auth)
        if r.status_code == 200:
            me = r.json()
            _pass("GET /auth/me", r.status_code, f"username={me['username']}")
            passed += 1
        else:
            _fail("GET /auth/me", r.status_code, r.text)

        # Step 4: Upload document
        r = client.post(
            "/documents/upload",
            headers=auth,
            files={"file": ("sample.txt", SAMPLE_DOC.encode(), "text/plain")},
        )
        if r.status_code == 200:
            data = r.json()
            document_id = data["id"]
            chunks = len(data.get("chunks", []))
            _pass("Upload document", r.status_code, f"doc_id={document_id}, chunks={chunks}")
            passed += 1
        else:
            _fail("Upload document", r.status_code, r.text)

        # Step 5: List documents
        r = client.get("/documents/", headers=auth)
        if r.status_code == 200:
            docs = r.json()
            _pass("List documents", r.status_code, f"{len(docs)} document(s)")
            passed += 1
        else:
            _fail("List documents", r.status_code, r.text)

        # Step 6: Ask a question
        if document_id is None:
            _fail("Ask question", 0, "skipped — no document_id (upload failed)")
        else:
            r = client.post(
                "/queries/ask",
                headers=auth,
                json={"document_id": document_id, "question": "What is RAG?"},
            )
            if r.status_code == 200:
                answer = r.json().get("answer", "")[:80].replace("\n", " ")
                _pass("Ask question", r.status_code, f'answer="{answer}..."')
                passed += 1
            else:
                _fail("Ask question", r.status_code, r.text)

        # Step 7: Query history
        r = client.get("/queries/history", headers=auth)
        if r.status_code == 200:
            history = r.json()
            _pass("Query history", r.status_code, f"{len(history)} record(s)")
            passed += 1
        else:
            _fail("Query history", r.status_code, r.text)

    _summarize(passed, total)
    return passed == total


def _summarize(passed: int, total: int) -> None:
    print(f"\n{'='*40}")
    print(f"  Result: {passed}/{total} steps passed")
    print(f"{'='*40}")


if __name__ == "__main__":
    print(f"\nRAG Search API — End-to-End Test")
    print(f"Target: {BASE_URL}\n")
    ok = run_tests()
    sys.exit(0 if ok else 1)
