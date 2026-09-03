/* The grind loop: a prefetch buffer, five keys, and a batched flush.
 *
 * The one number that matters here is that pressing the arrow key does ZERO
 * network work. The browser holds ten questions and refetches when four are
 * left, so the request that keeps the buffer full happens while the student is
 * reading question six, not while they are waiting for question seven. A
 * student who grinds two hundred questions in an evening should never see a
 * spinner and should never need the mouse.
 *
 * Three things in here are deliberate and easy to undo by accident:
 *
 *   1. Every fetch passes the ids the browser is already holding as `exclude`,
 *      so a refetch cannot hand back a question that is on screen or queued
 *      behind it.
 *   2. A seen event is queued when a question is DISPLAYED, not when it is
 *      answered, so closing the tab mid-question still records that it was
 *      shown. If an outcome arrives before that event flushes, the queued
 *      event is amended rather than a second one being sent, which keeps
 *      `times_seen` honest.
 *   3. The flush uses fetch(keepalive) and not navigator.sendBeacon, because
 *      the app requires an X-CSRF-Token header and sendBeacon cannot set one.
 */
(function () {
  "use strict";

  var app = document.getElementById("practiceApp");
  if (!app) { return; }

  var CSRF = app.dataset.csrf;
  var URL_SESSION = app.dataset.sessionUrl;
  var URL_NEXT = app.dataset.nextUrl;
  var URL_SEEN = app.dataset.seenUrl;
  var URL_RESET = app.dataset.resetUrl;

  // Buffer 10, refetch at 4. Ten is about a minute of grinding, which is long
  // enough that a slow response has time to land and short enough that a
  // student who changes topic has not made the server do much work for
  // nothing.
  var BUFFER_TARGET = 10;
  var REFETCH_AT = 4;
  var FLUSH_EVERY = 5;
  // Matches MAX_EXCLUDE in practice_views.py. Anything longer is trimmed here
  // rather than being silently dropped at the other end.
  var MAX_EXCLUDE = 120;

  var picker = document.getElementById("picker");
  var run = document.getElementById("run");
  var el = {
    crumb: document.getElementById("crumb"),
    runSub: document.getElementById("runSub"),
    notice: document.getElementById("notice"),
    qMeta: document.getElementById("qMeta"),
    qText: document.getElementById("qText"),
    qAnswer: document.getElementById("qAnswer"),
    qAnswerText: document.getElementById("qAnswerText"),
    qWorking: document.getElementById("qWorking"),
    qHidden: document.getElementById("qHidden"),
    score: document.getElementById("runScore"),
    resetBtn: document.getElementById("resetBtn"),
    pickerEmpty: document.getElementById("pickerEmpty")
  };

  var state = {
    subject: "",
    year: "",
    calculator: "",
    session: null,
    buffer: [],       // fetched, not yet shown
    history: [],      // shown, in order
    cursor: -1,       // index into history of what is on screen
    events: [],       // queued seen events, keyed by item id
    fetching: false,
    exhausted: false, // the server has nothing more for this scope
    answered: 0,
    correct: 0,
    remaining: 0
  };

  /* ------------------------------------------------------------- helpers */

  function show(node, visible) {
    if (node) { node.hidden = !visible; }
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(body),
      credentials: "same-origin"
    });
  }

  function setNotice(text) {
    if (!el.notice) { return; }
    el.notice.textContent = text || "";
    show(el.notice, !!text);
  }

  /* -------------------------------------------------------------- picker */

  function subjectPanels() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".scopeSubject"));
  }

  function applyFilters() {
    var visible = 0;
    subjectPanels().forEach(function (panel) {
      var chosen = panel.dataset.subject === state.subject;
      panel.hidden = !chosen;
      if (!chosen) { return; }
      var nodes = panel.querySelectorAll(".scopeNode");
      Array.prototype.forEach.call(nodes, function (node) {
        // A node carries every year it touches, so a strand that runs across
        // Year 11 and Year 12 survives both filters. Filtering on a year
        // parsed out of the scope id would hide Calculus from both.
        var years = (node.dataset.years || "").split("|").filter(Boolean);
        var keep = !state.year || years.length === 0
          || years.indexOf(state.year) !== -1;
        node.hidden = !keep;
        if (keep) { visible += 1; }
      });
    });
    show(el.pickerEmpty, visible === 0);
  }

  Array.prototype.forEach.call(
    document.querySelectorAll('input[name="practiceSubject"]'),
    function (input) {
      if (input.checked) { state.subject = input.value; }
      input.addEventListener("change", function () {
        state.subject = input.value;
        applyFilters();
      });
    });

  function wireChips(attr, onPick) {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-" + attr + "]"),
      function (chip) {
        chip.addEventListener("click", function () {
          var group = chip.parentNode;
          Array.prototype.forEach.call(group.children, function (sib) {
            sib.classList.remove("active");
          });
          chip.classList.add("active");
          onPick(chip.dataset[attr]);
        });
      });
  }

  wireChips("year", function (value) { state.year = value || ""; applyFilters(); });
  wireChips("calc", function (value) { state.calculator = value || ""; });

  Array.prototype.forEach.call(
    document.querySelectorAll(".scopePick"),
    function (button) {
      if (button.disabled) { return; }
      button.addEventListener("click", function () {
        startSession(button.dataset.scope, button.dataset.label);
      });
    });

  applyFilters();

  /* ------------------------------------------------------------- session */

  function startSession(scopeId, label) {
    flush(true);
    state.session = null;
    state.buffer = [];
    state.history = [];
    state.cursor = -1;
    state.events = [];
    state.exhausted = false;
    state.answered = 0;
    state.correct = 0;
    el.crumb.textContent = label || "Loading";
    el.qText.textContent = "";
    el.qMeta.textContent = "";
    setNotice("");
    show(picker, false);
    show(run, true);

    post(URL_SESSION, { scope_id: scopeId, calculator: state.calculator })
      .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
      .then(function (pair) {
        if (!pair[0]) {
          setNotice(pair[1].message || "That topic could not be opened.");
          return;
        }
        var data = pair[1];
        state.session = data.session_id;
        el.crumb.textContent = (data.breadcrumb || []).join("  >  ")
          || data.scope_label;
        el.runSub.textContent = describeScope(data);
        if (data.judge_only) {
          setNotice("Every topic in this selection is marked by explanation "
            + "rather than by calculation, so FolioAI does not stock questions "
            + "for it. Pick a topic that can be checked and the grind starts "
            + "straight away.");
          return;
        }
        refill();
      })
      .catch(function () {
        setNotice("The connection dropped before the session could start. "
          + "Try that topic again.");
      });
  }

  function describeScope(data) {
    var bits = [];
    if (data.subtopics) {
      bits.push(data.subtopics + (data.subtopics === 1 ? " topic" : " topics"));
    }
    if (data.depth) { bits.push(data.depth + " questions banked"); }
    if (data.calculator === "free") { bits.push("calculator free"); }
    if (data.calculator === "assumed") { bits.push("calculator assumed"); }
    return bits.join(", ");
  }

  /* --------------------------------------------------------------- fetch */

  function excludeIds() {
    // What the browser already holds: the queue, plus what is on screen and
    // recently behind it so pressing Back and then Next cannot collide with a
    // fresh draw.
    var ids = state.buffer.map(function (i) { return i.id; });
    ids = ids.concat(state.history.slice(-40).map(function (i) { return i.id; }));
    return ids.slice(-MAX_EXCLUDE);
  }

  function refill() {
    if (state.fetching || !state.session || state.exhausted) { return; }
    state.fetching = true;
    var url = URL_NEXT + "?session=" + encodeURIComponent(state.session)
      + "&n=" + BUFFER_TARGET
      + "&exclude=" + encodeURIComponent(excludeIds().join(","));
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) { throw new Error("draw failed"); }
        return r.json();
      })
      .then(function (data) {
        state.fetching = false;
        state.remaining = data.remaining_unseen || 0;
        setNotice(data.message || "");
        show(el.resetBtn, !!data.dry);
        var items = data.items || [];
        if (!items.length) {
          // Nothing came back and nothing is queued: the scope is genuinely
          // out. The server has already said why in `message`; it is never
          // widened to fill the gap.
          state.exhausted = state.buffer.length === 0;
          if (state.exhausted && state.cursor < 0) { renderEmpty(data); }
          return;
        }
        state.buffer = state.buffer.concat(items);
        if (state.cursor < 0) { advance(); } else { paintScore(); }
      })
      .catch(function () {
        state.fetching = false;
        setNotice("Could not reach the question bank. The next arrow press "
          + "will try again.");
      });
  }

  function renderEmpty(data) {
    el.qMeta.textContent = "";
    el.qText.textContent = data.unstocked
      ? "No questions are banked for this selection yet."
      : "Nothing more to serve here.";
    show(el.qAnswer, false);
    show(el.qHidden, false);
    paintScore();
  }

  /* ------------------------------------------------------------ movement */

  function current() {
    return state.cursor >= 0 ? state.history[state.cursor] : null;
  }

  function advance() {
    if (state.cursor < state.history.length - 1) {
      // Walking forward again after a Back. Already seen, already recorded.
      state.cursor += 1;
      paint();
      return;
    }
    if (!state.buffer.length) {
      // The buffer is the whole point: this branch should be unreachable
      // during a normal grind, and reaching it means the refetch has not
      // landed yet or the scope is out.
      refill();
      if (state.exhausted) { setNotice(el.notice.textContent
        || "You have reached the end of this selection."); }
      return;
    }
    var item = state.buffer.shift();
    state.history.push(item);
    state.cursor = state.history.length - 1;
    queueSeen(item.id, null);
    paint();
    if (state.buffer.length <= REFETCH_AT) { refill(); }
  }

  function back() {
    if (state.cursor > 0) { state.cursor -= 1; paint(); }
  }

  function paint() {
    var item = current();
    if (!item) { return; }
    el.qMeta.innerHTML = "";
    metaChip(item.subtopic);
    metaChip(item.difficulty);
    if (item.marks) { metaChip(item.marks + (item.marks === 1 ? " mark" : " marks")); }
    if (item.calculator === "free") { metaChip("calculator free"); }
    if (item.calculator === "assumed") { metaChip("calculator assumed"); }
    if (item.repeat) { metaChip("seen before", "repeatFlag"); }
    el.qText.textContent = item.question;
    el.qAnswerText.textContent = item.answer;
    el.qWorking.textContent = item.working || "";
    // Revealed state is per question, so pressing Next never shows the next
    // answer before the student has read the question.
    show(el.qAnswer, !!item._revealed);
    show(el.qHidden, !item._revealed);
    paintScore();
  }

  function metaChip(text, cls) {
    if (!text) { return; }
    var span = document.createElement("span");
    span.textContent = text;
    if (cls) { span.className = cls; }
    el.qMeta.appendChild(span);
  }

  function paintScore() {
    var seen = state.history.length;
    var bits = [seen + (seen === 1 ? " question" : " questions") + " shown"];
    if (state.answered) {
      bits.push(state.correct + " of " + state.answered + " marked right");
    }
    if (state.remaining) { bits.push(state.remaining + " unseen left"); }
    el.score.textContent = bits.join(".  ") + ".";
  }

  function reveal() {
    var item = current();
    if (!item) { return; }
    item._revealed = true;
    show(el.qAnswer, true);
    show(el.qHidden, false);
  }

  function mark(outcome) {
    var item = current();
    if (!item) { return; }
    if (!item._outcome) {
      state.answered += 1;
      if (outcome === "got_it") { state.correct += 1; }
    }
    item._outcome = outcome;
    queueSeen(item.id, outcome);
    reveal();
    paintScore();
  }

  /* ---------------------------------------------------------- seen flush */

  function queueSeen(itemId, outcome) {
    var existing = null;
    for (var i = 0; i < state.events.length; i += 1) {
      if (state.events[i].item_id === itemId) { existing = state.events[i]; break; }
    }
    if (existing) {
      // Amend rather than append. Two rows for one question would count the
      // question as seen twice.
      if (outcome) { existing.outcome = outcome; }
      return;
    }
    state.events.push({
      item_id: itemId, outcome: outcome || null,
      at: Math.floor(Date.now() / 1000)
    });
    if (state.events.length > FLUSH_EVERY) { flush(false); }
  }

  function flush(includeCurrent) {
    if (!state.session || !state.events.length) { return; }
    var live = current();
    var keep = [];
    var send = state.events;
    if (!includeCurrent && live) {
      // Hold back the question on screen: its outcome may still be coming,
      // and sending it now is what would produce a second event later.
      send = [];
      state.events.forEach(function (e) {
        if (e.item_id === live.id) { keep.push(e); } else { send.push(e); }
      });
    }
    if (!send.length) { return; }
    state.events = keep;
    post(URL_SEEN, { session_id: state.session, events: send })
      .catch(function () {
        // A failed flush is replayed on the next one. The server upserts on
        // (user_id, item_id), so a replay changes nothing.
        state.events = send.concat(state.events);
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "hidden") { return; }
    if (!state.session || !state.events.length) { return; }
    // keepalive, not sendBeacon: this app requires an X-CSRF-Token header on
    // every state-changing request and sendBeacon cannot set headers.
    fetch(URL_SEEN, {
      method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify({ session_id: state.session, events: state.events })
    }).catch(function () { /* replayed on the next flush */ });
    state.events = [];
  });

  /* ------------------------------------------------------------ controls */

  function on(id, handler) {
    var node = document.getElementById(id);
    if (node) { node.addEventListener("click", handler); }
  }

  on("nextBtn", function () { advance(); });
  on("prevBtn", function () { back(); });
  on("revealBtn", function () { reveal(); });
  on("gotBtn", function () { mark("got_it"); advance(); });
  on("missedBtn", function () { mark("missed"); advance(); });
  on("changeScope", function () { toPicker(); });
  on("resetBtn", function () {
    if (!state.session) { return; }
    post(URL_RESET, { session_id: state.session })
      .then(function () {
        state.buffer = [];
        state.history = [];
        state.cursor = -1;
        state.events = [];
        state.exhausted = false;
        setNotice("");
        show(el.resetBtn, false);
        refill();
      })
      .catch(function () { setNotice("That could not be cleared. Try again."); });
  });

  function toPicker() {
    flush(true);
    show(run, false);
    show(picker, true);
  }

  document.addEventListener("keydown", function (ev) {
    if (run.hidden) { return; }
    var tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return; }
    if (ev.metaKey || ev.ctrlKey || ev.altKey) { return; }
    switch (ev.key) {
      case " ":
      case "Spacebar":
        ev.preventDefault(); reveal(); break;
      case "ArrowRight":
      case "Enter":
        ev.preventDefault(); advance(); break;
      case "ArrowLeft":
        ev.preventDefault(); back(); break;
      case "ArrowUp":
        ev.preventDefault(); mark("got_it"); advance(); break;
      case "ArrowDown":
        ev.preventDefault(); mark("missed"); advance(); break;
      case "Escape":
        ev.preventDefault(); toPicker(); break;
      default:
        break;
    }
  });
}());
