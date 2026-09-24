// The conversation list: grouped by recency, the active one highlighted, open and delete actions.

import { element } from "./dom.js";

const DAY_MS = 24 * 60 * 60 * 1000;

export class Sidebar {
  constructor(container, { onOpen, onDelete }) {
    this._container = container;
    this._onOpen = onOpen;
    this._onDelete = onDelete;
  }

  render(conversations, activeId) {
    const groups = new Map();
    for (const conversation of conversations) {
      const label = recencyLabel(conversation.updated_at);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(conversation);
    }

    this._container.replaceChildren();
    if (!conversations.length) {
      this._container.append(element("p", "sidebar-empty", "No conversations yet."));
      return;
    }
    for (const [label, items] of groups) {
      this._container.append(element("h2", "group-label", label));
      for (const conversation of items) {
        this._container.append(this._item(conversation, conversation.id === activeId));
      }
    }
  }

  _item(conversation, active) {
    const open = element("button", "conversation-open", conversation.title);
    open.type = "button";
    open.title = conversation.title;
    open.addEventListener("click", () => this._onOpen(conversation.id));

    const remove = element("button", "conversation-delete", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete "${conversation.title}"`);
    remove.addEventListener("click", () => this._onDelete(conversation));

    const item = element("div", `conversation${active ? " active" : ""}`, [open, remove]);
    if (active) item.setAttribute("aria-current", "true");
    return item;
  }
}

function recencyLabel(timestamp) {
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  const time = new Date(timestamp).getTime();
  if (time >= startOfToday) return "Today";
  if (time >= startOfToday - DAY_MS) return "Yesterday";
  if (time >= startOfToday - 7 * DAY_MS) return "Previous 7 days";
  return "Older";
}
