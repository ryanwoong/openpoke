export function replyChoices(text: string): { text: string } | null {
  if (text.includes("```") || text.includes("~~~")) return null;
  const match = /(?:^|\n\s*\n)(?:Reply|Respond|Answer)\s+(?:with\s+)?yes\b[^\n.!?]*\bor\s+no\b[^\n.!?]*(?:[.!?]|$)/i.exec(text);
  if (!match || text.slice(match.index + match[0].length).includes("\n")) return null;
  return { text: (text.slice(0, match.index).trimEnd() + "\n\n" + text.slice(match.index + match[0].length).trim()).trim() };
}
