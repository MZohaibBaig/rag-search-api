"""Eval questions over app/demo_data/brindlemoor-lighthouse.txt.

`expected` lists source phrases copied verbatim from the document. A phrase counts as
retrieved when it appears (case-insensitive, whitespace-normalised) in a returned chunk.
A question with several phrases needs all of them for full recall. Unanswerable questions
have no phrases: the right behaviour is to abstain (no LLM call).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Q:
    kind: str
    question: str
    expected: tuple[str, ...] = ()

    @property
    def answerable(self) -> bool:
        return bool(self.expected)


QUESTIONS = [
    # single fact
    Q("single", "How tall is the tower?", ("38 metres tall",)),
    Q("single", "When did the lighthouse first shine?", ("14 October 1874",)),
    Q("single", "What was the name of the first head keeper?", ("Silas Threlfall",)),
    Q("single", "What kind of horn is the fog signal?", ("compressed-air horn",)),
    Q("single", "How does the light's flash pattern identify it to sailors?",
      ("two-flash pattern is what identifies Brindlemoor",)),
    # two-part / compound
    Q("compound", "Who designed Brindlemoor Lighthouse, and why was it built?",
      ("Ottoline Varga designed the tower", "commissioned the lighthouse after the steamship Halcyon Dawn")),
    Q("compound", "When was the lighthouse automated and who was the last keeper?",
      ("automated on 30 June 1989", "the last keeper, Dov Abramsen")),
    Q("compound", "How much did construction cost and how many granite blocks were used?",
      ("The final cost was 61,000 pounds", "4,100 granite blocks")),
    # two unrelated facts from different sections: the question embedding blends both topics
    Q("compound", "How many steps lead up to the lamp room, and what year was the fog signal upgraded?",
      ("176 steps", "upgraded from an explosive-charge system in 1932")),
    Q("compound", "What do grey seals do on the eastern ledges and how many gannet pairs nest there?",
      ("Grey seals haul out on the eastern ledges", "roughly 3,000 pairs")),
    # paraphrased, little or no keyword overlap with the source sentence
    Q("paraphrase", "Who was the architect behind this beacon?", ("Ottoline Varga designed the tower",)),
    Q("paraphrase", "What disaster prompted the building of the beacon?",
      ("Halcyon Dawn struck the Gannet Reefs",)),
    Q("paraphrase", "How long did a worker stay on the islet before getting time off?",
      ("four weeks on the rock followed by two weeks ashore",)),
    Q("paraphrase", "Which staff member earned an award for staying awake through a hurricane?",
      ("Ines Okonkwo-Ward",)),
    # numeric
    Q("numeric", "How many glass prisms did the Paris workshop ship?", ("1,008 individual glass prisms",)),
    Q("numeric", "How many steps lead up to the lamp room?", ("176 steps",)),
    Q("numeric", "What is the tidal range at spring tides?", ("6.2 metres",)),
    Q("numeric", "How many passengers can each boat trip carry?", ("at most 12 passengers",)),
    # unanswerable: two off-topic, two about the lighthouse but absent from the document
    Q("offtopic", "What is the best recipe for sourdough bread?"),
    Q("offtopic", "Who won the 2018 football World Cup?"),
    Q("near-domain", "How much does a ticket for the boat trip cost?"),
    Q("near-domain", "How many ships has the lighthouse rescued since 1989?"),
]
