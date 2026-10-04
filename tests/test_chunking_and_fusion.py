from app import rag
from app.chunking import chunk_text

DOC = """Fact Sheet

Overview
The tower is tall. It stands on a rock. Many ships pass it every night.

History
It was built in 1874. The engineer was careful. Work only happened in summer.

Another history paragraph follows here. It adds more detail about the build.
"""


def test_each_chunk_is_prefixed_with_title_and_section_heading():
    chunks = chunk_text(DOC)
    assert chunks[0].startswith("Fact Sheet - Overview\n")
    assert any(c.startswith("Fact Sheet - History\n") for c in chunks)


def test_chunks_never_cross_a_section_heading():
    for chunk in chunk_text(DOC):
        heading, body = chunk.split("\n", 1)
        if "tall" in body:
            assert heading.endswith("Overview")
        if "1874" in body or "careful" in body:
            assert heading.endswith("History")
        assert not ("tall" in body and "1874" in body)


def test_splits_on_sentence_boundaries_with_whole_sentence_overlap():
    sentences = [f"Sentence number {i} is here." for i in range(12)]
    chunks = chunk_text("Heading\n" + " ".join(sentences), chunk_size=100, overlap=60)
    assert len(chunks) > 2
    bodies = [c.split("\n", 1)[1] for c in chunks]
    for body in bodies:
        assert len(body) <= 100
        assert body.endswith(".") and body.startswith("Sentence")  # never cut mid-sentence
    for before, after in zip(bodies, bodies[1:]):
        assert before.split(". ")[-1].rstrip(".") in after  # last sentence repeats in the next chunk


def test_overlap_tail_is_dropped_when_it_would_push_a_chunk_over_chunk_size():
    # Each sentence is 57 chars: the tail fits the overlap (80) but tail + next sentence is 115 > 100.
    sentences = [("Abcdefghij " * 5).strip() + f" {i}." for i in range(6)]
    chunks = chunk_text("Heading\n" + " ".join(sentences), chunk_size=100, overlap=80)
    assert len(chunks) > 2
    for chunk in chunks:
        assert len(chunk.split("\n", 1)[1]) <= 100


def test_overlong_text_without_punctuation_is_wrapped_at_word_boundaries():
    chunks = chunk_text("word " * 400, chunk_size=200, overlap=0)
    assert len(chunks) > 5 and all(len(c) <= 200 for c in chunks)


def test_text_made_only_of_short_lines_still_produces_chunks():
    assert chunk_text("alpha beta\n\ngamma delta")


def test_empty_text_gives_no_chunks():
    assert chunk_text("  \n\n ") == []


def test_split_clauses_on_compound_questions():
    assert rag.split_clauses("Who designed Brindlemoor Lighthouse, and why was it built?") == [
        "Who designed Brindlemoor Lighthouse", "why was it built"]
    assert rag.split_clauses("How tall is the tower?") == ["How tall is the tower"]


def test_best_semantic_match_outside_fused_top_k_does_not_abstain(monkeypatch):
    # Chunk 1 is the closest semantically but fusion ranks chunks 2-6 above it
    # (they appear in both lists); those five are all far from the question.
    semantic = [(1, 0.1)] + [(i, 0.95) for i in range(2, 8)]
    monkeypatch.setattr(rag, "embed_text", lambda text: [0.0] * 384)
    monkeypatch.setattr(rag, "_semantic_search", lambda *a: semantic)
    monkeypatch.setattr(rag, "_keyword_search", lambda *a: [2, 3, 4, 5, 6, 7])

    class Chunk:
        def __init__(self, id):
            self.id = id

    class FakeQuery:
        def filter(self, *a):
            return self

        def all(self):
            return [(Chunk(i), 0.95) for i in range(2, 7)]

    class FakeDB:
        def query(self, *a):
            return FakeQuery()

    result = rag.retrieve(FakeDB(), document_id=1, question="how tall")
    assert [h.chunk.id for h in result.hits] == [2, 3, 4, 5, 6]  # chunk 1 really is displaced
    assert not result.abstain


def test_rrf_rewards_agreement_between_rankings():
    assert rag._rrf([[1, 2, 3], [3, 1, 4]])[:2] == [1, 3]  # 1 and 3 appear in both, 2 and 4 in one
    assert rag._rrf([[5], [6]]) == [5, 6]                  # ties break by id


def test_rrf_weights_favour_the_heavier_ranking():
    assert rag._rrf([[1, 2], [2, 1]], weights=[2.0, 1.0])[0] == 1
