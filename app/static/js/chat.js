// The message area: past turns, the turn being streamed, and the empty-state suggestions.
// Model and data text is only inserted via textContent or the escaping Markdown renderer.

import { element } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const EXAMPLE_QUESTIONS = [
  "Which traffic channels drove the most revenue in December 2020?",
  "What does the purchase funnel look like, and where do users drop off?",
  "Which products generate the most revenue?",
  "How do new and returning visitors compare on conversion?",
];
const PREVIEW_ROWS = 20;
const STICK_TO_BOTTOM_PX = 80;

export class ChatView {
  constructor(container, { onExampleQuestion }) {
    this._container = container;
    this._onExampleQuestion = onExampleQuestion;
  }

  /** Replace the view with a saved conversation, or the empty state if it has no turns. */
  showTurns(turns) {
    this._container.replaceChildren();
    if (!turns.length) {
      this._showEmptyState();
      return;
    }
    for (const turn of turns) {
      const view = this.startTurn(turn.question);
      for (const query of turn.queries) view.addSavedQuery(query);
      view.appendText(turn.answer);
      view.finish();
    }
    this._scrollToBottom(true);
  }

  /** Add a question and an empty answer that the returned TurnView fills in as events arrive. */
  startTurn(question) {
    this._container.querySelector(".empty-state")?.remove();
    const questionEl = element("div", "message user", question);
    const answerEl = element("div", "message assistant");
    this._container.append(questionEl, answerEl);
    this._scrollToBottom(true);
    return new TurnView(questionEl, answerEl, (force) => this._scrollToBottom(force));
  }

  _showEmptyState() {
    const buttons = EXAMPLE_QUESTIONS.map((question) => {
      const button = element("button", "example", question);
      button.type = "button";
      button.addEventListener("click", () => this._onExampleQuestion(question));
      return button;
    });
    this._container.append(element("div", "empty-state", [
      element("h2", "", "Ask about the Google Merchandise Store"),
      element("p", "", "GA4 ecommerce data, November 2020 – January 2021. For example:"),
      element("div", "examples", buttons),
    ]));
  }

  _scrollToBottom(force = false) {
    const el = this._container;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX;
    if (force || nearBottom) el.scrollTop = el.scrollHeight;
  }
}

/** One assistant answer, built up from streamed events in the order they arrive. */
class TurnView {
  constructor(questionEl, answerEl, scroll) {
    this._questionEl = questionEl;
    this._answerEl = answerEl;
    this._scroll = scroll;
    this._queries = new Map(); // tool_use id -> query card
    this._segment = null; // the text or progress block currently receiving deltas
    this._renderScheduled = false;
    this._startedAt = performance.now();
    answerEl.classList.add("pending");
  }

  appendText(text) {
    this._appendTo("answer-text", text);
  }

  appendProgress(text) {
    this._appendTo("progress", text);
  }

  queryStarted({ id, purpose, sql }) {
    const card = new QueryCard(purpose, sql, "running…");
    this._queries.set(id, card);
    this._appendBlock(card.element);
  }

  queryFinished(result) {
    this._queries.get(result.id)?.finish(result);
    this._scroll();
  }

  /** A query from a reloaded conversation: purpose and SQL are stored, result rows are not. */
  addSavedQuery({ purpose, query }) {
    this._appendBlock(new QueryCard(purpose, query, "").element);
  }

  finish() {
    this._render();
    this._answerEl.classList.remove("pending");
  }

  showDuration() {
    const seconds = ((performance.now() - this._startedAt) / 1000).toFixed(0);
    this._answerEl.append(element("div", "meta", `Answered in ${seconds}s`));
  }

  fail(message, onRetry) {
    this.finish();
    const retry = element("button", "retry", "Retry");
    retry.type = "button";
    retry.addEventListener("click", () => {
      this.remove();
      onRetry();
    });
    this._answerEl.append(element("div", "error", [element("span", "", message), retry]));
    this._scroll(true);
  }

  cancelled() {
    this.finish();
    this._answerEl.append(element("div", "meta", "Stopped. This answer was not saved."));
  }

  remove() {
    this._questionEl.remove();
    this._answerEl.remove();
  }

  // Consecutive deltas of the same kind (answer text or progress note) grow one block;
  // anything else in between (a query card, the other kind) starts a new one.
  _appendTo(kind, text) {
    if (!text) return;
    if (this._segment?.kind !== kind) {
      const el = element("div", kind);
      this._appendBlock(el);
      this._segment = { kind, source: "", el };
    }
    this._segment.source += text;
    this._scheduleRender();
  }

  _appendBlock(el) {
    this._render(); // flush the current segment before it stops receiving deltas
    this._segment = null;
    this._answerEl.append(el);
    this._scroll();
  }

  // Streaming delivers many small deltas; re-render at most once per animation frame.
  _scheduleRender() {
    if (this._renderScheduled) return;
    this._renderScheduled = true;
    requestAnimationFrame(() => this._render());
  }

  _render() {
    this._renderScheduled = false;
    const segment = this._segment;
    if (!segment) return;
    if (segment.kind === "answer-text") segment.el.innerHTML = renderMarkdown(segment.source);
    else segment.el.textContent = segment.source;
    this._scroll();
  }
}

/** A collapsible card: what a query checks, its status, and (when opened) SQL and result rows. */
class QueryCard {
  constructor(purpose, sql, status) {
    this._status = element("span", "query-status", status);
    this._body = element("div", "query-body", [element("pre", "", element("code", "", sql))]);
    this.element = element("details", "query", [
      element("summary", "", [element("span", "query-purpose", purpose || "Query"), this._status]),
      this._body,
    ]);
  }

  finish(result) {
    if (result.error) {
      this.element.classList.add("failed");
      this._status.textContent = "error";
      this._body.append(element("p", "query-error", result.error));
      return;
    }
    const shown = result.truncated ? `, first ${result.rows.length}` : "";
    this._status.textContent = `${result.total_rows} rows${shown} · ${result.mb_scanned} MB`;
    this._body.append(resultTable(result.columns, result.rows.slice(0, PREVIEW_ROWS)));
    if (result.rows.length > PREVIEW_ROWS) {
      this._body.append(element("p", "meta", `Showing ${PREVIEW_ROWS} of ${result.rows.length} rows.`));
    }
  }
}

function resultTable(columns, rows) {
  const head = element("tr", "", columns.map((column) => element("th", "", column)));
  const body = rows.map((row) => element("tr", "", columns.map((column) =>
    element("td", "", formatValue(row[column])))));
  return element("div", "table-wrap", element("table", "", [
    element("thead", "", head), element("tbody", "", body),
  ]));
}

function formatValue(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString();
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
