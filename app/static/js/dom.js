// DOM helper shared by the views.

/** Create an element with a class and children (strings become text nodes, never HTML). */
export function element(tag, className = "", children = []) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  el.append(...[children].flat());
  return el;
}
