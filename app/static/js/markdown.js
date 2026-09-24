// A small, safe Markdown renderer for the subset the model writes: paragraphs, headings,
// bold/italic, inline code, code blocks, lists (nested), tables, blockquotes, rules and links.
//
// Safety: all text is HTML-escaped before any tags are added, so model output can never inject
// markup or scripts. Links are only created for http(s) URLs.

const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const HORIZONTAL_RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const FENCE = /^\s*(```|~~~)/;

export function renderMarkdown(source) {
  return renderBlocks(source.replace(/\r\n?/g, "\n").split("\n"));
}

export function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// ---- Blocks ------------------------------------------------------------------------------

function renderBlocks(lines) {
  const html = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
    } else if (FENCE.test(line)) {
      const fence = line.trim().slice(0, 3);
      const end = findIndex(lines, i + 1, (l) => l.trim().startsWith(fence));
      const stop = end === -1 ? lines.length : end;
      html.push(`<pre><code>${escapeHtml(lines.slice(i + 1, stop).join("\n"))}</code></pre>`);
      i = stop + 1;
    } else if (HEADING.test(line)) {
      const [, hashes, text] = line.match(HEADING);
      const level = Math.min(hashes.length + 2, 6); // keep headings small inside a chat bubble
      html.push(`<h${level}>${renderInline(text)}</h${level}>`);
      i += 1;
    } else if (HORIZONTAL_RULE.test(line)) {
      html.push("<hr>");
      i += 1;
    } else if (isTableStart(lines, i)) {
      const end = findIndex(lines, i + 2, (l) => !l.includes("|") || !l.trim());
      html.push(renderTable(lines.slice(i, end === -1 ? lines.length : end)));
      i = end === -1 ? lines.length : end;
    } else if (line.trimStart().startsWith(">")) {
      const end = findIndex(lines, i, (l) => !l.trimStart().startsWith(">"));
      const quoted = lines.slice(i, end === -1 ? lines.length : end);
      html.push(`<blockquote>${renderBlocks(quoted.map((l) => l.replace(/^\s*>\s?/, "")))}</blockquote>`);
      i = end === -1 ? lines.length : end;
    } else if (LIST_ITEM.test(line)) {
      const { html: listHtml, next } = renderList(lines, i);
      html.push(listHtml);
      i = next;
    } else {
      const end = findIndex(lines, i + 1, (l, j) => !l.trim() || startsBlock(lines, j));
      const paragraph = lines.slice(i, end === -1 ? lines.length : end);
      html.push(`<p>${renderInline(paragraph.map((l) => l.trim()).join(" "))}</p>`);
      i = end === -1 ? lines.length : end;
    }
  }
  return html.join("");
}

function startsBlock(lines, i) {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || HORIZONTAL_RULE.test(line)
    || LIST_ITEM.test(line) || line.trimStart().startsWith(">") || isTableStart(lines, i);
}

function isTableStart(lines, i) {
  return lines[i].includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1]);
}

function renderList(lines, start) {
  const baseIndent = indentOf(lines[start]);
  const [, , firstMarker] = lines[start].match(LIST_ITEM);
  const ordered = isOrderedItem(lines[start]);
  // A sibling item continues this list only if it uses the same kind of marker.
  const isSibling = (line) => LIST_ITEM.test(line) && indentOf(line) === baseIndent
    && isOrderedItem(line) === ordered;
  const items = []; // each item: [first line text, ...continuation / nested lines]
  let i = start;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      // A blank line ends the list unless the list continues right after it.
      const next = findIndex(lines, i + 1, (l) => l.trim());
      if (next !== -1 && (isSibling(lines[next]) || indentOf(lines[next]) > baseIndent)) {
        i = next;
        continue;
      }
      break;
    }
    const indent = indentOf(line);
    if (isSibling(line)) {
      items.push([line.match(LIST_ITEM)[3]]);
    } else if (indent > baseIndent && items.length) {
      const item = items.at(-1);
      // A wrapped line continues the item's first sentence; anything else is nested content.
      if (item.length === 1 && !startsBlock([line.trim()], 0)) item[0] += ` ${line.trim()}`;
      else item.push(line);
    } else {
      break;
    }
    i += 1;
  }

  const tag = ordered ? "ol" : "ul";
  const startNumber = parseInt(firstMarker, 10);
  const startAttr = ordered && startNumber !== 1 ? ` start="${startNumber}"` : "";
  const body = items.map(([first, ...rest]) =>
    `<li>${renderInline(first)}${rest.length ? renderBlocks(dedent(rest)) : ""}</li>`).join("");
  return { html: `<${tag}${startAttr}>${body}</${tag}>`, next: i };
}

function renderTable(lines) {
  const [header, separator, ...rows] = lines.map(splitRow);
  const alignments = separator.map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    return right ? "right" : "";
  });
  const cell = (tag, text, col) => {
    const align = alignments[col] ? ` style="text-align:${alignments[col]}"` : "";
    return `<${tag}${align}>${renderInline(text)}</${tag}>`;
  };
  const head = `<tr>${header.map((text, col) => cell("th", text, col)).join("")}</tr>`;
  const body = rows.map((row) =>
    `<tr>${header.map((_, col) => cell("td", row[col] ?? "", col)).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

function splitRow(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((cell) => cell.trim());
}

// ---- Inline ------------------------------------------------------------------------------

function renderInline(text) {
  // Code spans and backslash escapes are finished HTML: stash them behind placeholders so the
  // emphasis and link rules below can't touch their contents.
  const stash = [];
  const keep = (html) => `\u0000${stash.push(html) - 1}\u0000`;

  let html = text
    .replace(/`([^`]+)`/g, (_, code) => keep(`<code>${escapeHtml(code)}</code>`))
    .replace(/\\([\\`*_{}[\]()#+\-.!|<>~])/g, (_, char) => keep(escapeHtml(char)));

  html = escapeHtml(html)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      (_, label, url) => keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`))
    .replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "<strong>$1</strong>")
    .replace(/__(?=\S)(.+?)(?<=\S)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*(?=\S)(.+?)(?<=\S)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^_\w])_(?=\S)(.+?)(?<=\S)_(?![_\w])/g, "$1<em>$2</em>");

  // Placeholders can contain other placeholders (e.g. code inside a link label).
  while (html.includes("\u0000")) {
    html = html.replace(/\u0000(\d+)\u0000/g, (_, index) => stash[Number(index)]);
  }
  return html;
}

// ---- Helpers -----------------------------------------------------------------------------

function findIndex(lines, from, predicate) {
  for (let i = from; i < lines.length; i += 1) {
    if (predicate(lines[i], i)) return i;
  }
  return -1;
}

function isOrderedItem(line) {
  return /^\s*\d/.test(line);
}

function indentOf(line) {
  return line.length - line.trimStart().length;
}

function dedent(lines) {
  const indent = Math.min(...lines.filter((l) => l.trim()).map(indentOf));
  return lines.map((l) => l.slice(indent));
}
