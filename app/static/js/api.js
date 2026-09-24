// Calls to the backend API. The chat endpoint streams Server-Sent Events over a POST response,
// which the browser's EventSource can't do (GET only), so the stream is read from fetch directly.

export class ApiError extends Error {}

export const getSession = () => request("/api/session");
export const listConversations = () => request("/api/conversations");
export const newChat = () => request("/api/session/new", { method: "POST" });
export const openConversation = (id) => request(`/api/conversations/${id}/open`, { method: "POST" });
export const deleteConversation = (id) => request(`/api/conversations/${id}`, { method: "DELETE" });

/** Ask a question; yields {event, data} objects until the server closes the stream. */
export async function* streamChat(question, signal) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.ok) throw new ApiError(await errorMessage(response));

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    const { events, rest } = parseEventStream(buffer);
    buffer = rest;
    yield* events;
  }
}

/**
 * Split buffered Server-Sent Events text into complete events plus the unfinished remainder.
 * Events are separated by a blank line; each has an "event:" line and a JSON "data:" line.
 */
export function parseEventStream(text) {
  const blocks = text.split("\n\n");
  const rest = blocks.pop();
  const events = blocks.filter((block) => block.trim()).map(parseEvent);
  return { events, rest };
}

function parseEvent(block) {
  let event = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart());
  }
  return { event, data: JSON.parse(data.join("\n")) };
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) throw new ApiError(await errorMessage(response));
  return response.status === 204 ? null : response.json();
}

async function errorMessage(response) {
  try {
    const { detail } = await response.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg).join("; "); // validation errors
  } catch {
    // Not JSON; fall through to the generic message.
  }
  return `Request failed (${response.status})`;
}
