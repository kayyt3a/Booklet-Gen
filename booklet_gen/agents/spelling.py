"""Weekly spelling: a list at the back of one booklet, a test at the front of
the next.

Spelling used to be taught through worked examples, which is a category error.
A worked example derives a method, and there is no method to derive for whether
"black" takes a c: the model duly produced an example explaining that "blak"
was missing an e. Spelling is a memorisation routine that runs across a term,
so it is modelled as one: 20 words set this week, 12 of them tested next week.

Two properties matter more than the wording of any individual list:

  THE TEST IS ON WORDS THE STUDENT WAS GIVEN
      `make_test` samples from the previous booklet's list. It never generates
      words, so it cannot test something never set. `from_week` records which
      booklet the words came from.

  NO WEEK REPEATS ANOTHER
      A term plan that sets the same 20 words in week 3 and week 7 is broken:
      the student does the second week for free. `generate_list` is told what
      has already been set, filters the reply against it in code rather than
      trusting the model, and tops up from a fallback bank.

The fallback bank also carries the whole feature when the model call fails, the
same way `TermPlannerAgent` falls back to a deterministic ramp. It is finite:
about five weeks per year band. Past that a week's list comes back short rather
than repeating, because a short honest list is recoverable and a repeat is not.
"""
from __future__ import annotations

import logging
import random
import re

from ..llm import LLMClient
from ..schemas import SpellingList, SpellingTest
from ._shared import extract_json

log = logging.getLogger(__name__)

LIST_SIZE = 20
TEST_SIZE = 12

# Kept inline rather than in prompts/ because it is small, has no variants, and
# the prompt directory is a shared surface. Move it to prompts/spelling.txt if
# it ever needs tuning independently of this code.
_SYSTEM = """You set the weekly spelling list for an Australian school practice booklet.

Return ONLY a single JSON object, no prose and no code fences:
  {"words": ["...", "..."]}

Rules:
- Australian English spelling (colour, favourite, realise, practise the verb).
- Single words only. No phrases, no hyphenated pairs, no proper nouns.
- Pitch the difficulty at the year level you are given: words a student at that
  level will meet in reading and get wrong in writing, not words they already
  spell without thinking and not words they have no use for.
- Build the list around a pattern or two so it can be taught, not just tested:
  a sound, a suffix, a prefix, a word family, a common exception.
- Return exactly the number of words asked for, all different from each other.
- You are given the words already set earlier in the term. Do not return any of
  them, and do not return a near-identical form of one (a list that has "happy"
  should not come back with "happier")."""

# Roughly five weeks of fallback words per band. Ordered so that consecutive
# slices hold together as a teachable group.
_BANK: tuple[tuple[int, int, tuple[str, ...]], ...] = (
    (1, 2, (
        "cat", "hat", "mat", "sat", "pin", "win", "tin", "bin", "hop", "top",
        "mop", "stop", "bed", "red", "fed", "led", "cup", "pup", "sun", "run",
        "ship", "shop", "shut", "shed", "chin", "chip", "chop", "chat", "then",
        "them", "this", "that", "thin", "path", "with", "bath", "wish", "fish",
        "rich", "such", "make", "cake", "lake", "take", "bike", "like", "time",
        "line", "hope", "note", "rope", "home", "cube", "tube", "rain", "pain",
        "train", "chain", "boat", "coat", "road", "soap", "seed", "feed",
        "tree", "free", "night", "light", "right", "bright", "play", "stay",
        "day", "way", "book", "look", "took", "good", "moon", "soon", "food",
        "room", "cars", "dogs", "boxes", "wishes", "jumped", "jumping",
        "walked", "walking", "happy", "funny", "sunny", "little", "apple",
        "table", "under", "over", "after", "because", "friend",
    )),
    (3, 4, (
        "answer", "castle", "listen", "island", "climb", "thumb", "wrist",
        "wrong", "knock", "knife", "knee", "gnaw", "half", "calm", "talk",
        "walk", "chalk", "folk", "would", "could", "should", "caught",
        "taught", "brought", "thought", "bought", "fought", "enough", "rough",
        "tough", "cough", "laugh", "young", "double", "trouble", "country",
        "cousin", "money", "honey", "monkey", "chimney", "valley", "trolley",
        "beautiful", "careful", "hopeful", "useful", "painful", "helpful",
        "sadness", "kindness", "darkness", "illness", "quickly", "slowly",
        "gently", "simply", "happily", "angrily", "heavier", "heaviest",
        "carried", "hurried", "married", "copied", "planning", "swimming",
        "running", "stopping", "shopping", "clapped", "dropped", "grabbed",
        "february", "january", "wednesday", "saturday", "morning", "evening",
        "picture", "future", "nature", "creature", "adventure", "furniture",
        "special", "official", "station", "action", "question", "mention",
        "circle", "centre", "certain", "science", "scissors", "muscle",
        "receive", "believe", "achieve", "piece", "field",
    )),
    (5, 6, (
        "accommodate", "achievement", "acquire", "address", "aggressive",
        "amateur", "ancient", "apparent", "appreciate", "attached",
        "available", "average", "awkward", "bargain", "bruise", "category",
        "cemetery", "committee", "communicate", "community", "competition",
        "conscience", "conscious", "controversy", "convenience", "correspond",
        "criticise", "curiosity", "definite", "desperate", "determined",
        "develop", "dictionary", "disastrous", "embarrass", "environment",
        "equipped", "especially", "exaggerate", "excellent", "existence",
        "explanation", "familiar", "foreign", "forty", "frequently",
        "government", "guarantee", "harass", "hindrance", "identity",
        "immediately", "individual", "interfere", "interrupt", "language",
        "leisure", "lightning", "marvellous", "mischievous", "muscle",
        "necessary", "neighbour", "nuisance", "occasion", "occupy", "occur",
        "opportunity", "parliament", "persuade", "physical", "prejudice",
        "privilege", "profession", "programme", "pronunciation",
        "queue", "recognise", "recommend", "relevant", "restaurant", "rhyme",
        "rhythm", "sacrifice", "secretary", "shoulder", "signature",
        "sincerely", "soldier", "stomach", "sufficient", "suggest",
        "symbol", "system", "temperature", "thorough", "twelfth", "variety",
        "vegetable", "vehicle", "yacht",
    )),
    (7, 10, (
        "abbreviate", "aberration", "abstinence", "accessible", "accumulate",
        "acknowledge", "acquiesce", "adolescent", "aesthetic", "aggravate",
        "allegiance", "ambiguous", "anomaly", "antagonise", "apparatus",
        "arbitrary", "articulate", "assassinate", "atrocious", "auxiliary",
        "belligerent", "beneficiary", "bureaucracy", "camaraderie",
        "catastrophe", "circumstance", "coincidence", "colleague",
        "commemorate", "competent", "conceivable", "condemn", "confectionery",
        "connoisseur", "conscientious", "consensus", "contemporary",
        "counterfeit", "deceitful", "deficient", "deteriorate", "discipline",
        "discrepancy", "eligible", "eloquent", "embarrassment", "empirical",
        "entrepreneur", "exhilarate", "exorbitant", "extraordinary",
        "facilitate", "fascinate", "fluorescent", "gauge", "grievance",
        "haemorrhage", "hierarchy", "hypocrisy", "idiosyncrasy",
        "illegitimate", "impeccable", "inaugurate", "incidentally",
        "indispensable", "inoculate", "irresistible", "jeopardy", "labyrinth",
        "liaison", "lieutenant", "maintenance", "manoeuvre", "millennium",
        "miscellaneous", "negligible", "noticeable", "obsolete", "occurrence",
        "ostensible", "paraphernalia", "perseverance", "phenomenon",
        "plagiarise", "possession", "precedence", "predecessor", "prerogative",
        "questionnaire", "reconnaissance", "reminiscent", "rescind",
        "resuscitate", "silhouette", "supersede", "surveillance", "susceptible",
        "unanimous", "unprecedented", "vacuum", "vengeance",
    )),
)


def _year_number(year_level: str) -> int:
    """The numeric year, defaulting to a middle-primary band.

    Year levels arrive as free text ("Year 5", "year 5", "5"), so read the
    first number and clamp it. Defaulting matters more than parsing: an
    unrecognised year must still get a list.
    """
    m = re.search(r"\d+", year_level or "")
    if not m:
        return 5
    return max(1, min(10, int(m.group())))


def _bank_for(year_level: str) -> tuple[str, ...]:
    year = _year_number(year_level)
    for lo, hi, words in _BANK:
        if lo <= year <= hi:
            return words
    return _BANK[-1][2]


def _norm(word: str) -> str:
    return re.sub(r"[^a-z]", "", (word or "").strip().lower())


class SpellingAgent:
    """Sets a week's spelling list and the following week's test."""

    def __init__(self, client: LLMClient, max_retries: int = 3,
                 list_size: int = LIST_SIZE, test_size: int = TEST_SIZE):
        self._client = client
        self._max_retries = max(1, max_retries)
        self._list_size = max(1, list_size)
        self._test_size = max(1, test_size)

    # ---------- this week's list ----------

    def generate_list(
        self,
        year_level: str,
        week: int,
        already_set: list[str] | None = None,
        focus: str | None = None,
    ) -> SpellingList:
        """The 20 words printed at the back of week `week`'s booklet.

        `already_set` is every word set earlier in the term. It goes into the
        prompt AND is enforced in code afterwards: the instruction lowers the
        collision rate, the filter is what makes "no week repeats another"
        true. A model that ignores the instruction costs a top-up from the
        bank, not a broken term plan.
        """
        used = {_norm(w) for w in (already_set or []) if _norm(w)}
        from_model = self._dedupe(self._ask(year_level, week, already_set or [], focus), used)
        picked = from_model[:self._list_size]
        if len(picked) < self._list_size:
            picked = self._dedupe(
                picked + self._bank_words(year_level, used, picked), used,
            )[:self._list_size]
            log.info("spelling.topped_up_from_bank",
                     extra={"week": week, "from_model": len(from_model),
                            "final": len(picked)})
        if len(picked) < self._list_size:
            # The bank for this year band is exhausted. A short list is a
            # visible, recoverable defect; repeating an earlier week is not.
            log.warning("spelling.short_list",
                        extra={"week": week, "year_level": year_level,
                               "count": len(picked), "asked_for": self._list_size})
        log.info("spelling.list",
                 extra={"week": week, "year_level": year_level,
                        "count": len(picked), "asked_for": self._list_size})
        return SpellingList(words=picked)

    def _ask(self, year_level, week, already_set, focus) -> list[str]:
        user = (
            f"Year level: {year_level}\n"
            f"Week of the term: {week}\n"
            f"Number of words: {self._list_size}\n"
        )
        if focus:
            user += (
                f"This week's English focus is: {focus}\n"
                "Where it fits naturally, let a few words come from that focus. "
                "Do not force it: the list is a spelling list first.\n"
            )
        if already_set:
            user += (
                "\nWords already set earlier this term. None of these, and "
                "nothing that is just one of them with an ending changed:\n"
                + ", ".join(already_set)
            )
        error = ""
        for attempt in range(1, self._max_retries + 1):
            msg = user if not error else (
                f"{user}\n\nYour previous attempt failed:\n{error}\nReturn corrected JSON."
            )
            try:
                raw = self._client.complete(_SYSTEM, msg, tier="fast", temperature=0.7)
                data = extract_json(raw)
                words = [w for w in (data.get("words") or []) if isinstance(w, str)]
                if not words:
                    raise ValueError("empty words array")
                return words
            except ValueError as e:
                error = str(e)
                log.warning("spelling.retry",
                            extra={"week": week, "attempt": attempt, "error": error[:200]})
            except Exception as e:  # client/network failure: retrying the same call is pointless
                log.warning("spelling.client_error",
                            extra={"week": week, "error": str(e)[:200]})
                break
        log.warning("spelling.fallback", extra={"week": week})
        return []

    def _dedupe(self, words: list[str], used: set[str]) -> list[str]:
        """Keep the first spelling of each new word, in order."""
        out: list[str] = []
        seen: set[str] = set()
        for w in words:
            key = _norm(w)
            if not key or key in used or key in seen:
                continue
            seen.add(key)
            out.append(w.strip())
        return out

    def _bank_words(self, year_level, used: set[str], already: list[str]) -> list[str]:
        taken = used | {_norm(w) for w in already}
        return [w for w in _bank_for(year_level) if _norm(w) not in taken]

    # ---------- next week's test ----------

    def make_test(self, previous: SpellingList | None, from_week: int | None,
                  size: int | None = None) -> SpellingTest | None:
        """The 12-word test printed at the front of the NEXT booklet.

        Drawn from `previous`, never generated, so a test can only ever ask for
        words that were actually set. Returns None when there is no previous
        list, which is week 1: a first booklet has a list and no test.

        The sample is seeded by the week it came from, so regenerating week 4
        produces the same test rather than a different one, and the words are
        returned in list order so a parent can mark against the sheet the
        student studied.
        """
        words = [w for w in (previous.words if previous else []) if (w or "").strip()]
        if not words:
            return None
        n = min(size or self._test_size, len(words))
        rng = random.Random(f"{from_week}:{'|'.join(words)}")
        idx = sorted(rng.sample(range(len(words)), n))
        return SpellingTest(words=[words[i] for i in idx], from_week=from_week)
