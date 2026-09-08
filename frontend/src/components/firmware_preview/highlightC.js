/* Coloration syntaxique C maison — regex unique à alternatives.
 * Jetons : commentaires, préprocesseur (#define/#include…), chaînes,
 * nombres, mots-clés, types. Retourne une liste [{ text, cls }] à mapper
 * en <span> par le composant.
 */

const KEYWORDS = new Set([
  'auto', 'break', 'case', 'continue', 'default', 'do', 'else', 'enum', 'extern',
  'for', 'goto', 'if', 'inline', 'register', 'restrict', 'return', 'sizeof',
  'static', 'struct', 'switch', 'typedef', 'union', 'volatile', 'while', 'asm',
]);

const TYPES = new Set([
  'void', 'char', 'short', 'int', 'long', 'float', 'double', 'signed', 'unsigned',
  'bool', 'const', 'size_t', 'ssize_t', 'ptrdiff_t', 'intptr_t', 'uintptr_t',
  'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t',
  'int8_t', 'int16_t', 'int32_t', 'int64_t',
]);

/* ordre des alternatives = priorité :
 * 1 commentaires  2 préprocesseur  3 chaînes  4 nombres  5 identifiants */
const MASTER = new RegExp(
  [
    '(\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*)', // 1 commentaires
    '(^[ \\t]*#[^\\n]*)', // 2 préprocesseur (début de ligne)
    '("(?:\\\\.|[^"\\\\])*"|\'(?:\\\\.|[^\'\\\\])*\')', // 3 chaînes / caractères
    '(\\b0[xX][0-9a-fA-F]+[uUlL]*\\b|\\b\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?[uUlLfF]*\\b)', // 4 nombres
    '([A-Za-z_]\\w*)', // 5 identifiants
  ].join('|'),
  'gm'
);

/**
 * Tokenise du code C.
 * @param {string} code
 * @returns {Array<{text: string, cls: string}>}
 */
export function tokenizeC(code) {
  const source = String(code || '');
  const out = [];
  let last = 0;
  let match;
  MASTER.lastIndex = 0;
  while ((match = MASTER.exec(source)) !== null) {
    if (match.index > last) out.push({ text: source.slice(last, match.index), cls: 'tok-id' });
    const [full, comment, pre, str, num, word] = match;
    if (comment) out.push({ text: full, cls: 'tok-comment' });
    else if (pre) out.push({ text: full, cls: 'tok-pre' });
    else if (str) out.push({ text: full, cls: 'tok-str' });
    else if (num) out.push({ text: full, cls: 'tok-num' });
    else if (word) {
      if (KEYWORDS.has(word)) out.push({ text: full, cls: 'tok-kw' });
      else if (TYPES.has(word)) out.push({ text: full, cls: 'tok-type' });
      else if (source[match.index + full.length] === '(') out.push({ text: full, cls: 'tok-fn' });
      else out.push({ text: full, cls: 'tok-id' });
    }
    last = match.index + full.length;
    if (full.length === 0) MASTER.lastIndex += 1; // garde-fou boucle infinie
  }
  if (last < source.length) out.push({ text: source.slice(last), cls: 'tok-id' });
  return out;
}

/** Code → lignes tokenisées (une ligne = liste de spans). */
export function tokenizeCPerLine(code) {
  const tokens = tokenizeC(code);
  const lines = [[]];
  for (const token of tokens) {
    const parts = String(token.text).split('\n');
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ text: part, cls: token.cls });
    });
  }
  return lines;
}
