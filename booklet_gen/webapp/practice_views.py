"""The grind: pick a scope, press an arrow, get another verified question.

Five routes and a picker page. Everything a student presses lands here, and the
one architectural rule that holds the whole feature up is that **no request on
this path may call a language model**. The bank is filled overnight by
`booklet_gen.practice.filler`; a request only ever reads it. That is what makes
the arrow key feel instant, and `scripts/check_practice_api.py` proves it by
making `booklet_gen.llm.get_client` raise and confirming `/practice/next` still
serves.

Two more rules that are easy to break later and expensive to notice:

* **The scope is never widened.** A student who picked Antidifferentiation and
  runs out of unseen questions is told they have run out and offered the same
  set again. They are never quietly handed a neighbouring topic. The one
  feature whose entire promise is the scope cannot be the feature that lies
  about it.
* **The four empty states are four different messages.** Unseen stock, going
  round again, nothing banked yet, and a scope where every subtopic is
  judge-only are distinct facts about the bank, and collapsing them into one
  "no questions" screen tells a student nothing they can act on.

THE BANK INTERFACE THIS FILE EXPECTS
------------------------------------
`booklet_gen/practice/store.py` belongs to another builder and is imported
lazily, inside functions, so this blueprint can be registered and this module
imported before the bank exists. The calls made are:

    init_practice_db()                                        -> None
    create_session(user_id, subject, scope_id, scope_label,
                   calculator)                                -> str
    get_session(session_id)                                   -> mapping | None
    draw(user_id, leaf_ids, limit, exclude=(), calculator=None) -> DrawResult
    record_seen(user_id, events, session_id=...)              -> int
    reset_seen(user_id, leaf_ids)                             -> int
    note_scope_demand(subtopic_id, dry=False)                 -> None
    bank_depth(leaf_ids)                                      -> int | mapping

Where the plan does not fix a name, a short alias list is accepted, and a
missing call degrades to an honest "practice is not available yet" rather than
a 500. `DrawResult`, `ItemRow.for_client` and `SeenEvent` come from
`practice/models.py`, which is the fixed contract.
"""
from __future__ import annotations

import logging
import time

from flask import (
    Blueprint, abort, g, jsonify, render_template, request,
)

from .auth import login_required
from .security import enforce_rate_limit
from .. import senior_syllabus as syl

bp = Blueprint("practice", __name__)
log = logging.getLogger(__name__)

# How many questions one draw may hand the browser. The client asks for 10 and
# refetches at 4; the ceiling is here so a hand-edited query string cannot turn
# one request into a bank dump.
MAX_BATCH = 20
DEFAULT_BATCH = 10

# The browser passes the ids it is already holding so the server never
# re-serves them. A buffer of 10 plus a little history is the real size; the
# cap stops an unbounded IN list arriving from outside the app.
MAX_EXCLUDE = 120

# One flush carries at most this many seen events. The client flushes every 5.
MAX_SEEN_EVENTS = 200

# Rate limits. `practice-next` is 600 an hour, which is 6000 questions an hour
# at a batch of ten and cannot be reached by a person reading them. The session
# limit is what actually protects the database, because a session insert is the
# only write on the picker path.
RL_SESSION = ("practice-session", 60, 3600)
RL_NEXT = ("practice-next", 600, 3600)
# A flush is one write per five answers, so it is bounded by how fast a human
# can read. Generous, but not unbounded.
RL_SEEN = ("practice-seen", 600, 3600)
# Reset throws away history, so it is deliberately the tightest of the four.
RL_RESET = ("practice-reset", 40, 3600)


# ---------------------------------------------------------------------------
# The bank, imported lazily
# ---------------------------------------------------------------------------

def _store():
    """The bank module, or None if it is not on this checkout yet.

    Lazy on purpose. `store.py` declares a schema and opens the shared
    connection path, and a web process that never serves a practice request
    should not pay for either at import time. It also means this blueprint can
    be registered against a tree where the bank has not landed.
    """
    try:
        from ..practice import store  # noqa: WPS433 (deliberate local import)
        return store
    except Exception as exc:  # pragma: no cover - only on an incomplete tree
        log.warning("practice bank unavailable: %s", exc)
        return None


def _fn(*names):
    """The first of `names` the bank actually defines, or None.

    The plan fixes `draw`, `note_scope_demand` and `bank_depth` by name and
    leaves the session and seen helpers unnamed. Accepting a short alias list
    is cheaper than guessing wrong and shipping a blueprint that raises
    AttributeError on the first click.
    """
    store = _store()
    if store is None:
        return None
    for name in names:
        found = getattr(store, name, None)
        if callable(found):
            return found
    return None


def init_practice_db() -> None:
    """Create the bank's tables, beside `init_db()` in `create_app`.

    Never raises. The practice bank is one feature; a schema problem in it must
    not stop the app that sells booklets from booting.
    """
    creator = _fn("init_practice_db", "init_db")
    if creator is None:
        log.info("practice bank not installed; /practice will report that")
        return
    try:
        creator()
    except Exception:
        log.exception("could not initialise the practice bank")


def _unavailable():
    """What every route says when the bank is not installed.

    503 and not 500: nothing is broken, the feature is not deployed here.
    """
    return jsonify({
        "error": "unavailable",
        "message": ("Practice questions are not available on this deployment "
                    "yet."),
    }), 503


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

def _subject_from_request(raw: str) -> str:
    """A subject name from either the display name or the slug.

    The picker posts "Mathematics Methods"; a stored scope id starts
    "methods:". Both have to resolve, and anything else has to resolve to
    nothing rather than to the first subject in the dict.
    """
    raw = (raw or "").strip()
    if raw in syl.SUBJECTS:
        return raw
    return syl.subject_for_key(raw)


def _subject_of_scope(scope_id: str) -> str:
    key = (scope_id or "").split(":")[0].split(".")[0]
    return syl.subject_for_key(key)


def _tree(scopes):
    """Nest a flat `scope_options()` list using `Scope.parent`.

    `scope_options` guarantees every parent is present in the same response, so
    this is a single pass with no lookups that can fail. Rows whose parent is
    missing would silently vanish, so they are attached to the root instead:
    the picker showing a topic under the wrong heading is recoverable, the
    picker not showing a topic at all is not.
    """
    nodes = {s.id: {"scope": s, "children": []} for s in scopes}
    roots = []
    for s in scopes:
        node = nodes[s.id]
        parent = nodes.get(s.parent) if s.parent else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


def _breadcrumb(scope_id: str) -> list[str]:
    """Labels from the subject down to the chosen scope.

    Printed above the question card so a student five minutes into a session
    can still see exactly what they asked for.
    """
    subject = _subject_of_scope(scope_id)
    if not subject:
        return []
    by_id = {s.id: s for s in syl.scope_options(subject)}
    trail: list[str] = []
    seen: set[str] = set()
    cursor = scope_id
    while cursor and cursor in by_id and cursor not in seen:
        seen.add(cursor)
        trail.append(by_id[cursor].label)
        cursor = by_id[cursor].parent or ""
    trail.reverse()
    return trail or [syl.scope_label(scope_id)]


def _scope_state(scope_id: str) -> tuple[list[str], str]:
    """The bankable leaves of a scope, and why the list is empty if it is.

    Three outcomes, and telling them apart is the whole reason this returns a
    reason string rather than just a list:

      ("ok")          real leaves to draw from
      ("judge_only")  the scope exists and every subtopic in it can only be
                      marked by a language model, so it is deliberately never
                      stocked
      ("unknown")     no such scope. A stale bookmark or a hand-edited id.
    """
    leaves = syl.resolve_scope(scope_id, bankable_only=True)
    if leaves:
        return leaves, "ok"
    if syl.resolve_scope(scope_id, bankable_only=False):
        return [], "judge_only"
    return [], "unknown"


def _depth(leaf_ids) -> int:
    """How many live questions the bank holds across these subtopics."""
    counter = _fn("bank_depth", "depth")
    if counter is None or not leaf_ids:
        return 0
    try:
        value = counter(list(leaf_ids))
    except Exception:
        log.exception("bank_depth failed")
        return 0
    if isinstance(value, dict):
        return int(sum(int(v or 0) for v in value.values()))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _note_demand(leaf_ids, dry: bool) -> None:
    """Tell the filler which subtopics students actually grind.

    Called on session creation (so `requests` counts real intent) and on every
    dry or unstocked draw (so `dry_requests` counts real disappointment). It is
    deliberately NOT called on a healthy draw: that path runs every six
    questions and must stay a read.
    """
    noter = _fn("note_scope_demand", "note_demand")
    if noter is None:
        return
    for leaf in leaf_ids:
        try:
            noter(leaf, dry=dry)
        except TypeError:
            try:
                noter(leaf)
            except Exception:
                log.debug("note_scope_demand(%s) failed", leaf, exc_info=True)
                return
        except Exception:
            log.debug("note_scope_demand(%s) failed", leaf, exc_info=True)
            return


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@bp.route("/practice")
@login_required
def practice():
    """The picker, with the whole tree already rendered.

    Both subjects are rendered server side, nested, with the one not chosen
    hidden. Rendering it rather than building it in JavaScript is what makes
    the tree measurable: `scripts/check_practice_picker.py` reads the levels
    out of the response body, so a picker that collapses to a flat list fails a
    check instead of shipping.
    """
    subjects = []
    for name in syl.SUBJECTS:
        scopes = syl.scope_options(name)
        subjects.append({
            "name": name,
            "key": syl.SUBJECT_KEYS.get(name, ""),
            "tree": _tree(scopes),
            "bankable": sum(1 for s in syl.subtopics(name) if syl.bankable(s)),
            "total": len(syl.subtopics(name)),
        })
    return render_template(
        "practice.html",
        subjects=subjects,
        years=list(syl.UNITS_BY_YEAR),
        attribution=syl.ATTRIBUTION,
        installed=_store() is not None,
    )


@bp.get("/practice/scopes")
@login_required
def scopes():
    """The same tree as JSON.

    It exists so the picker check can measure the data and the rendered page
    against each other, and so a future client (a phone app, a keyboard-only
    launcher) does not have to scrape HTML.
    """
    subject = _subject_from_request(request.args.get("subject", ""))
    if not subject:
        abort(404)
    year = (request.args.get("year") or "").strip() or None
    if year and year not in syl.UNITS_BY_YEAR:
        abort(404)
    rows = [
        {"id": s.id, "label": s.label, "level": s.level, "count": s.count,
         "parent": s.parent}
        for s in syl.scope_options(subject, year)
    ]
    return jsonify({
        "subject": subject,
        "year": year,
        "scopes": rows,
        "attribution": syl.ATTRIBUTION,
    })


# ---------------------------------------------------------------------------
# The grind
# ---------------------------------------------------------------------------

@bp.post("/practice/session")
@login_required
def start_session():
    """Begin a run over one scope.

    Answers with what the student needs to decide whether to start: the label,
    the breadcrumb that proves the scope was understood, and how deep the bank
    actually is. A scope with nothing in it says so here, before the student
    has pressed anything.
    """
    enforce_rate_limit(*RL_SESSION)
    if _store() is None:
        return _unavailable()
    body = request.get_json(silent=True) or {}
    scope_id = str(body.get("scope_id") or "").strip()[:200]
    subject = _subject_of_scope(scope_id)
    if not subject:
        return jsonify({"error": "unknown_scope",
                        "message": "That topic is not part of the course."}), 404

    leaves, reason = _scope_state(scope_id)
    if reason == "unknown":
        return jsonify({"error": "unknown_scope",
                        "message": "That topic is not part of the course."}), 404

    calculator = str(body.get("calculator") or "").strip().lower()
    if calculator not in ("free", "assumed"):
        calculator = ""

    label = syl.scope_label(scope_id)
    creator = _fn("create_session", "start_session", "new_session")
    if creator is None:
        return _unavailable()
    try:
        session_id = creator(int(g.user["id"]), subject, scope_id, label,
                             calculator or None)
    except Exception:
        log.exception("could not create a practice session")
        return _unavailable()

    _note_demand(leaves, dry=False)
    depth = _depth(leaves)
    return jsonify({
        "session_id": str(session_id),
        "subject": subject,
        "scope_id": scope_id,
        "scope_label": label,
        "breadcrumb": _breadcrumb(scope_id),
        "calculator": calculator,
        "subtopics": len(leaves),
        "depth": depth,
        "stocked": bool(depth),
        "judge_only": reason == "judge_only",
    })


def _owned_session(session_id: str):
    """The session row, or a 404.

    Another account's session id is a 404 and not a redirect and not somebody
    else's questions. A student's practice history says what they are weak at,
    which is exactly the kind of thing that must not leak sideways.
    """
    getter = _fn("get_session", "session", "load_session")
    if getter is None:
        return None
    try:
        row = getter(session_id)
    except Exception:
        log.exception("could not read practice session")
        abort(404)
    if row is None:
        abort(404)
    try:
        owner = int(row["user_id"])
    except (KeyError, IndexError, TypeError, ValueError):
        abort(404)
    if not g.user or owner != int(g.user["id"]):
        abort(404)
    return row


def _row_value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _parse_exclude(raw: str) -> list[int]:
    out: list[int] = []
    for chunk in (raw or "").split(",")[:MAX_EXCLUDE]:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return out


@bp.get("/practice/next")
@login_required
def next_questions():
    """The arrow's supply. Reads the bank. Calls no model, ever.

    The browser holds ten and refetches at four, passing the ids it is holding
    as `exclude`, so a refetch cannot hand back a question already on screen.
    """
    enforce_rate_limit(*RL_NEXT)
    if _store() is None:
        return _unavailable()
    row = _owned_session(str(request.args.get("session") or "")[:80])
    if row is None:
        return _unavailable()

    scope_id = str(_row_value(row, "scope_id", ""))
    leaves, reason = _scope_state(scope_id)
    if reason == "judge_only":
        _note_demand(syl.resolve_scope(scope_id, bankable_only=False), dry=True)
        return jsonify(_empty_payload(
            "judge_only",
            ("Every topic in this selection is marked by explanation rather "
             "than by calculation, so FolioAI does not stock questions for it. "
             "Pick a topic that can be checked and the grind starts straight "
             "away."),
        ))
    if reason == "unknown":
        abort(404)

    try:
        want = int(request.args.get("n") or DEFAULT_BATCH)
    except ValueError:
        want = DEFAULT_BATCH
    want = max(1, min(MAX_BATCH, want))
    exclude = _parse_exclude(request.args.get("exclude", ""))
    calculator = str(_row_value(row, "calculator", "") or "")

    drawer = _fn("draw")
    if drawer is None:
        return _unavailable()
    dropped_by_calculator = False
    try:
        result = drawer(int(g.user["id"]), leaves, want, exclude=exclude,
                        calculator=calculator or None)
    except TypeError:
        # A bank that does not filter on calculator yet. Draw without it and
        # filter here rather than quietly serving calculator-assumed questions
        # to somebody who asked for calculator-free. A short batch is honest;
        # the wrong questions are not.
        result = drawer(int(g.user["id"]), leaves, want, exclude=exclude)
        dropped_by_calculator = bool(calculator)
    except Exception:
        log.exception("practice draw failed")
        return _unavailable()

    items = list(getattr(result, "items", []) or [])
    if dropped_by_calculator:
        items = [i for i in items
                 if getattr(i, "calculator", "either") in (calculator, "either")]

    repeats = set(getattr(result, "repeats", ()) or ())
    dry = bool(getattr(result, "dry", False))
    unstocked = bool(getattr(result, "unstocked", False)) or (
        not items and not dry)

    payload = {
        "items": [_client_item(i, i.id in repeats) for i in items],
        "remaining_unseen": int(getattr(result, "remaining_unseen", 0) or 0),
        "dry": dry,
        "unstocked": bool(unstocked and not items),
        "judge_only": False,
        "spacing": str(getattr(result, "spacing", "strict") or "strict"),
        "scope_label": syl.scope_label(scope_id),
        "message": "",
    }
    if payload["unstocked"]:
        _note_demand(leaves, dry=True)
        payload["message"] = (
            "There are no questions banked for this selection yet. It is a "
            "real part of the course and it is queued to be filled; a wider "
            "topic will have questions now.")
    elif dry:
        _note_demand(leaves, dry=True)
        payload["message"] = (
            "You have worked through every question FolioAI has for this "
            "selection. These are the ones you have seen already, oldest "
            "first.")
    return jsonify(payload)


def _empty_payload(kind: str, message: str) -> dict:
    return {
        "items": [], "remaining_unseen": 0,
        "dry": kind == "dry",
        "unstocked": kind == "unstocked",
        "judge_only": kind == "judge_only",
        "spacing": "strict", "scope_label": "", "message": message,
    }


def _client_item(item, repeat: bool) -> dict:
    """One question as the browser is allowed to see it.

    `ItemRow.for_client` is the contract, and it deliberately withholds
    `check_json` and `params_json`: those are how a question is verified and
    regenerated, and publishing them hands a student the answer to every
    question in the family rather than to this one.
    """
    sub = syl.subtopic(getattr(item, "subtopic_id", ""))
    name = sub.name if sub is not None else ""
    return item.for_client(name, repeat)


@bp.post("/practice/seen")
@login_required
def seen():
    """Record what the student was shown and how it went.

    Batched (the client flushes every five) and idempotent: a replay after a
    flaky connection is an upsert on `(user_id, item_id)`, so it changes
    nothing. That is what lets the client retry without keeping a ledger.
    """
    enforce_rate_limit(*RL_SEEN)
    if _store() is None:
        return _unavailable()
    body = request.get_json(silent=True) or {}
    row = _owned_session(str(body.get("session_id") or "")[:80])
    if row is None:
        return _unavailable()

    from ..practice.models import SeenEvent

    events = []
    raw_events = body.get("events")
    if not isinstance(raw_events, list):
        raw_events = []
    now = int(time.time())
    for raw in raw_events[:MAX_SEEN_EVENTS]:
        if not isinstance(raw, dict):
            continue
        try:
            item_id = int(raw.get("item_id"))
        except (TypeError, ValueError):
            continue
        outcome = raw.get("outcome")
        outcome = str(outcome) if outcome else None
        try:
            at = int(raw.get("at") or now)
        except (TypeError, ValueError):
            at = now
        event = SeenEvent(item_id=item_id, outcome=outcome, at=at)
        if event.valid():
            events.append(event)

    if not events:
        return jsonify({"recorded": 0})

    writer = _fn("record_seen", "mark_seen", "note_seen")
    if writer is None:
        return _unavailable()
    session_id = str(_row_value(row, "id", ""))
    try:
        try:
            written = writer(int(g.user["id"]), events, session_id=session_id)
        except TypeError:
            written = writer(int(g.user["id"]), events)
    except Exception:
        log.exception("could not record practice seen events")
        return _unavailable()
    return jsonify({"recorded": int(written or 0) or len(events)})


@bp.post("/practice/reset")
@login_required
def reset():
    """Deliberately go round again.

    Seen history is per user and not per session, so a student who comes back
    tomorrow is not handed the same twenty questions by accident. Clearing it
    is therefore a button they press, never something the server decides for
    them, and it only ever clears the subtopics inside the scope they are
    working on.
    """
    enforce_rate_limit(*RL_RESET)
    if _store() is None:
        return _unavailable()
    body = request.get_json(silent=True) or {}
    row = _owned_session(str(body.get("session_id") or "")[:80])
    if row is None:
        return _unavailable()
    leaves, reason = _scope_state(str(_row_value(row, "scope_id", "")))
    if reason == "unknown":
        abort(404)
    clearer = _fn("reset_seen", "reset_scope", "clear_seen")
    if clearer is None:
        return _unavailable()
    try:
        cleared = clearer(int(g.user["id"]), leaves)
    except Exception:
        log.exception("could not reset practice history")
        return _unavailable()
    return jsonify({"cleared": int(cleared or 0), "scope_subtopics": len(leaves)})
