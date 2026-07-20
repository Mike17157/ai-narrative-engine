// Shared formatter for chat / roleplay model output — the ONE place every chat surface
// (LlmConsole, Player) turns raw model text into rendered HTML. Models emit
// emphasis the markdown way: *italics* and **bold** (roleplay actions like *[he smiles]*
// included), plus `inline code`. We convert those to <em>/<strong>/<code>.
//
// SAFETY: the text is untrusted, so it is HTML-escaped FIRST; only our own tags are
// introduced afterward. That keeps the @html sink at the call sites safe.
//
// Newlines are intentionally left as-is — callers render inside `white-space: pre-wrap`,
// so paragraph breaks are preserved without injecting <br>.

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };

// Private-Use-Area sentinels that bracket extracted code spans. Built with fromCharCode
// (pure-ASCII source) so they can never appear in real model text, never be touched by the
// emphasis passes, and never collide the way a space-delimited number would.
const SO = String.fromCharCode(0xe000);
const SC = String.fromCharCode(0xe001);
const RESTORE = new RegExp(SO + '(\\d+)' + SC, 'g');

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ESC[c]);
}

/**
 * Format a chat message body into safe HTML with emphasis applied.
 * @param {string} text raw model output
 * @returns {string} HTML-safe string for use with {@html ...}
 */
export function formatChat(text) {
  if (!text) return '';
  let s = escapeHtml(String(text));

  // Pull inline-code spans out first so emphasis markers inside them stay literal.
  const code = [];
  s = s.replace(/`([^`\n]+?)`/g, (_, c) => SO + (code.push(c) - 1) + SC);

  // **bold** before *italic* so the double markers win.
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>');

  // *italic* — opener must be followed by a non-space and closer preceded by one, so
  // bullet lists ("* item") and stray "a * b" multiplication never match. This also
  // covers the *[bracketed action]* convention models use for stage directions.
  s = s.replace(/\*(?=\S)([\s\S]*?\S)\*/g, '<em>$1</em>');

  // _italic_ with word-boundary guards so snake_case identifiers are left alone.
  s = s.replace(/(^|[^\w*])_(?=\S)([\s\S]*?\S)_(?=[^\w*]|$)/g, '$1<em>$2</em>');

  // Restore the code spans.
  s = s.replace(RESTORE, (_, i) => '<code>' + code[+i] + '</code>');

  return s;
}
