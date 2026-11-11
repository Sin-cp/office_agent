export interface InlineToken {
  /** "caret" is a render-only marker for the typewriter; it never comes from parsing. */
  type: "text" | "strong" | "code" | "caret";
  text: string;
}

export interface MarkdownBlock {
  type: "paragraph" | "heading" | "list";
  tokens: InlineToken[];
  ordered: boolean;
  items: InlineToken[][];
}

function inlineTokens(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  const pattern = /\*\*([\s\S]+?)\*\*|`([^`\n]+)`/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    if (match.index > cursor)
      tokens.push({ type: "text", text: text.slice(cursor, match.index) });
    if (match[1] !== undefined)
      tokens.push({ type: "strong", text: match[1] });
    else tokens.push({ type: "code", text: match[2] ?? "" });
    cursor = pattern.lastIndex;
  }
  if (cursor < text.length)
    tokens.push({ type: "text", text: text.slice(cursor) });
  return tokens;
}

function paragraph(tokens: InlineToken[]): MarkdownBlock {
  return { type: "paragraph", tokens, ordered: false, items: [] };
}

function heading(tokens: InlineToken[]): MarkdownBlock {
  return { type: "heading", tokens, ordered: false, items: [] };
}

function list(ordered: boolean, items: InlineToken[][]): MarkdownBlock {
  return { type: "list", tokens: [], ordered, items };
}

/**
 * A deliberately small Markdown subset for model answers: paragraphs, headings,
 * unordered/ordered lists, bold and inline code. No HTML is interpreted.
 */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let paragraphLines: string[] = [];
  let listState: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (!paragraphLines.length) return;
    const text = paragraphLines.join("\n").trim();
    if (text) blocks.push(paragraph(inlineTokens(text)));
    paragraphLines = [];
  };
  const flushList = () => {
    if (!listState) return;
    blocks.push(list(listState.ordered, listState.items.map(inlineTokens)));
    listState = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const headingMatch = line.match(/^\s*#{1,6}\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      blocks.push(heading(inlineTokens(headingMatch[1].trim())));
      continue;
    }

    const boldHeading = line.match(/^\s*\*\*(.+?)\*\*\s*$/);
    if (boldHeading) {
      flushParagraph();
      flushList();
      blocks.push(heading(inlineTokens(boldHeading[1].trim())));
      continue;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const nextOrdered = !!ordered;
      if (listState && listState.ordered !== nextOrdered) flushList();
      if (!listState) listState = { ordered: nextOrdered, items: [] };
      listState.items.push((unordered?.[1] ?? ordered?.[1] ?? "").trim());
      continue;
    }

    // Indented continuation of the previous list item.
    if (listState && /^\s{2,}\S/.test(line) && listState.items.length) {
      listState.items[listState.items.length - 1] += `\n${line.trim()}`;
      continue;
    }

    flushList();
    paragraphLines.push(line);
  }

  flushParagraph();
  flushList();
  return blocks;
}
