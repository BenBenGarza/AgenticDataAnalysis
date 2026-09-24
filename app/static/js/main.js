// App controller: wires the API to the views and owns the UI state.

import * as api from "./api.js";
import { ChatView } from "./chat.js";
import { Sidebar } from "./sidebar.js";

const $ = (id) => document.getElementById(id);

const state = {
  conversation: null, // {id, title, ...} of the active conversation, or null for a new chat
  conversations: [],
  busy: false, // a question is being answered; the backend handles one at a time
  abort: null, // AbortController for the answer in progress
};

const chat = new ChatView($("messages"), { onExampleQuestion: ask });
const sidebar = new Sidebar($("conversation-list"), {
  onOpen: openConversation,
  onDelete: deleteConversation,
});

// ---- Asking ------------------------------------------------------------------------------

async function ask(question) {
  if (state.busy || !question.trim()) return;
  setBusy(true);
  const turn = chat.startTurn(question);
  const retry = () => ask(question);
  state.abort = new AbortController();

  let finished = false;
  try {
    for await (const { event, data } of api.streamChat(question, state.abort.signal)) {
      switch (event) {
        case "text": turn.appendText(data.text); break;
        case "progress": turn.appendProgress(data.text); break;
        case "query_started": turn.queryStarted(data); break;
        case "query_finished": turn.queryFinished(data); break;
        case "chart": turn.addChart(data.chart); break;
        case "done":
          finished = true;
          turn.finish();
          turn.showDuration();
          setConversation(data.conversation);
          refreshConversations();
          break;
        case "error":
          finished = true;
          turn.fail(data.message, retry);
          break;
      }
    }
    if (!finished) turn.fail("The connection closed before the answer finished.", retry);
  } catch (error) {
    if (error.name === "AbortError") turn.cancelled();
    else turn.fail(error.message || "Could not reach the server.", retry);
  } finally {
    state.abort = null;
    setBusy(false);
  }
}

// ---- Conversations -----------------------------------------------------------------------

async function loadActiveConversation() {
  const session = await api.getSession();
  setConversation(session.conversation);
  chat.showTurns(session.turns);
}

async function openConversation(id) {
  if (state.busy || id === state.conversation?.id) return closeSidebarOnMobile();
  const session = await guard(api.openConversation(id));
  if (!session) return;
  setConversation(session.conversation);
  chat.showTurns(session.turns);
  closeSidebarOnMobile();
}

async function startNewChat() {
  if (state.busy) return;
  if (await guard(api.newChat()) === undefined) return;
  setConversation(null);
  chat.showTurns([]);
  closeSidebarOnMobile();
  $("question").focus();
}

async function deleteConversation(conversation) {
  if (state.busy || !confirm(`Delete "${conversation.title}"?`)) return;
  if (await guard(api.deleteConversation(conversation.id)) === undefined) return;
  if (conversation.id === state.conversation?.id) {
    setConversation(null);
    chat.showTurns([]);
  }
  await refreshConversations();
}

async function refreshConversations() {
  const conversations = await guard(api.listConversations());
  if (!conversations) return;
  state.conversations = conversations;
  renderSidebar();
}

function setConversation(conversation) {
  state.conversation = conversation;
  $("conversation-title").textContent = conversation?.title ?? "New conversation";
  document.title = conversation ? `${conversation.title} · GA4 Analyst` : "GA4 Analyst";
  renderSidebar();
}

function renderSidebar() {
  sidebar.render(state.conversations, state.conversation?.id);
}

/** Await an API call; on failure show the message and resolve to undefined. */
async function guard(promise) {
  try {
    return await promise;
  } catch (error) {
    alert(error.message);
    return undefined;
  }
}

// ---- Composer ----------------------------------------------------------------------------

const input = $("question");
const sendButton = $("send");

function setBusy(busy) {
  state.busy = busy;
  sendButton.textContent = busy ? "Stop" : "Send";
  sendButton.classList.toggle("stop", busy);
  document.body.classList.toggle("busy", busy);
  // Switching or starting a chat mid-answer is refused (by this page and by the server).
  $("sidebar").title = busy ? "Wait for the answer to finish, or press Stop." : "";
}

function submit() {
  if (state.busy) {
    state.abort?.abort(); // the server rolls back the unfinished turn
    return;
  }
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  resizeInput();
  ask(question);
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
}

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  submit();
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
});
input.addEventListener("input", resizeInput);

// ---- Sidebar on small screens ------------------------------------------------------------

function closeSidebarOnMobile() {
  document.body.classList.remove("sidebar-open");
}

$("toggle-sidebar").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
$("sidebar-backdrop").addEventListener("click", closeSidebarOnMobile);
$("new-chat").addEventListener("click", startNewChat);

// ---- Start -------------------------------------------------------------------------------

await Promise.all([guard(loadActiveConversation()), refreshConversations()]);
input.focus();
