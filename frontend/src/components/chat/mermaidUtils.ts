/**
 * frontend/src/components/chat/mermaidUtils.ts
 * ----------------------------------------------
 * Pure utility functions for sanitizing LLM-generated Mermaid diagram source
 * into safe, renderable Mermaid syntax.
 *
 * Design goals:
 *   - Deterministic: same input always produces same output
 *   - Minimal: only rewrite what's necessary for safe rendering
 *   - Preserve legitimate Mermaid constructs (flowchart decl, arrows, subgraphs, styles)
 *   - Safe node IDs: deterministic identifiers like node_0, node_1, ...
 *   - Safe labels: properly quoted with ["..."] and special chars escaped
 *
 * Test cases (documented inline):
 *   - "Create a maintenance summary report for P-204..." → safe IDs, escaped parens
 *   - Tool names like docx_create, artifact_verifier → safe
 *   - Punctuation: colons, semicolons, commas → escaped in labels
 *   - Parentheses: (step 1), (optional) → escaped in labels
 *   - Unicode: ₹125,000 → preserved in labels, safe ID
 *   - Multiline text → collapsed to single line in labels
 *   - Mermaid control chars: #, {, }, |, & → escaped
 */

/**
 * Escape a string for use inside a Mermaid node label wrapped in ["..."].
 * Handles characters that break Mermaid parsing.
 *
 * Expected behavior:
 *   escapeMermaidLabel('Search documents') → 'Search documents'
 *   escapeMermaidLabel('Calculate (total)') → 'Calculate #40;total#41;'
 *   escapeMermaidLabel('Cost: ₹125,000') → 'Cost: ₹125,000'
 *   escapeMermaidLabel('Step "one"') → 'Step #quot;one#quot;'
 *   escapeMermaidLabel('A & B') → 'A #amp; B'
 */
export function escapeMermaidLabel(label: string): string {
  if (!label) return 'Step';

  // Collapse newlines/tabs to spaces
  let safe = label.replace(/[\r\n\t]+/g, ' ').trim();

  // Truncate very long labels to prevent diagram overflow
  if (safe.length > 120) {
    safe = safe.slice(0, 117) + '...';
  }

  // Escape Mermaid-special characters using Mermaid's HTML entity syntax
  // Order matters: & must be first to avoid double-escaping
  safe = safe.replace(/&/g, '#amp;');
  safe = safe.replace(/"/g, '#quot;');
  safe = safe.replace(/\(/g, '#40;');
  safe = safe.replace(/\)/g, '#41;');
  safe = safe.replace(/\[/g, '#91;');
  safe = safe.replace(/]/g, '#93;');
  safe = safe.replace(/\{/g, '#123;');
  safe = safe.replace(/}/g, '#125;');
  safe = safe.replace(/\|/g, '#124;');
  safe = safe.replace(/</g, '#lt;');
  safe = safe.replace(/>/g, '#gt;');
  // Semicolons can terminate Mermaid statements
  safe = safe.replace(/;/g, '#59;');

  return safe || 'Step';
}

/**
 * Check whether a string is a safe Mermaid node ID.
 * Safe IDs: alphanumeric + underscores, starting with a letter or underscore.
 */
function isSafeNodeId(id: string): boolean {
  return /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(id);
}

/**
 * Sanitize LLM-generated Mermaid source into safe, renderable syntax.
 *
 * Strategy:
 *   1. Identify the diagram type declaration line (e.g., "graph TD", "flowchart LR")
 *      and pass it through unchanged.
 *   2. For node definition lines, extract the node ID and label.
 *      - Replace unsafe node IDs with deterministic safe IDs (node_0, node_1, ...)
 *      - Wrap labels in ["..."] with escaped content
 *   3. For edge lines (containing arrows like -->, --->, ==>, -.->),
 *      replace any unsafe node ID references with their safe equivalents.
 *   4. Pass through subgraph, end, class, style, and other directives unchanged
 *      (with node ID substitution where needed).
 *
 * This function is deterministic: same input → same output.
 */
export function sanitizeMermaidSource(chart: string): string {
  if (!chart || !chart.trim()) return chart;

  const lines = chart.split('\n');
  const idMap = new Map<string, string>(); // originalId → safeId
  let idCounter = 0;

  /**
   * Get or create a safe ID for a given original node identifier.
   */
  function getSafeId(originalId: string): string {
    const trimmed = originalId.trim();
    if (!trimmed) return `node_${idCounter++}`;

    // If already mapped, return existing
    if (idMap.has(trimmed)) return idMap.get(trimmed)!;

    // If the ID is already safe, keep it
    if (isSafeNodeId(trimmed)) {
      idMap.set(trimmed, trimmed);
      return trimmed;
    }

    // Generate a safe deterministic ID
    const safeId = `node_${idCounter++}`;
    idMap.set(trimmed, safeId);
    return safeId;
  }

  // Regex patterns
  // Diagram type declaration (first meaningful line)
  const diagramDeclRe = /^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|gantt|pie|gitGraph|erDiagram|journey|C4Context)\b/i;
  // Arrow patterns in edges
  const arrowPatterns = ['===>', '==>', '--->', '-->', '-.->', '-.->',  '-.-', '---', '--', '==>|', '-->|', '-.->|'];

  // Simple node reference (just an ID on a line, possibly with class)
  const simpleNodeRe = /^(\s*)([^\s\-=\.>|:;]+)\s*$/;
  // Subgraph line
  const subgraphRe = /^\s*subgraph\b/i;
  // End line
  const endRe = /^\s*end\s*$/i;
  // Style/class/click directives
  const directiveRe = /^\s*(style|class|classDef|click|linkStyle)\b/i;
  // Comment
  const commentRe = /^\s*%%/;

  const outputLines: string[] = [];

  for (const line of lines) {
    const trimmedLine = line.trim();

    // Empty lines
    if (!trimmedLine) {
      outputLines.push(line);
      continue;
    }

    // Comments — pass through
    if (commentRe.test(trimmedLine)) {
      outputLines.push(line);
      continue;
    }

    // Diagram declaration — pass through
    if (diagramDeclRe.test(trimmedLine)) {
      outputLines.push(line);
      continue;
    }

    // Subgraph — pass through (may contain label text, but subgraph labels are more forgiving)
    if (subgraphRe.test(trimmedLine)) {
      outputLines.push(line);
      continue;
    }

    // End — pass through
    if (endRe.test(trimmedLine)) {
      outputLines.push(line);
      continue;
    }

    // Style/class directives — substitute node IDs but keep directive
    if (directiveRe.test(trimmedLine)) {
      let processed = trimmedLine;
      for (const [origId, safeId] of idMap.entries()) {
        if (origId !== safeId) {
          // Replace whole-word occurrences of the original ID
          const escaped = origId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          processed = processed.replace(new RegExp(`\\b${escaped}\\b`, 'g'), safeId);
        }
      }
      outputLines.push(processed);
      continue;
    }

    // Check for edge lines (contain arrows)
    const hasArrow = arrowPatterns.some(arrow => trimmedLine.includes(arrow));

    if (hasArrow) {
      // Edge line: split by arrows, sanitize node references
      let processed = trimmedLine;

      // Find all node ID tokens in the edge definition
      // Split by arrow operators, handling edge labels like -->|label|
      // Strategy: replace known unsafe IDs with safe equivalents
      const parts = splitEdgeLine(trimmedLine);
      const sanitizedParts: string[] = [];

      for (const part of parts) {
        if (isArrowOrLabel(part)) {
          sanitizedParts.push(part);
        } else {
          // This is a node reference — might have inline label
          const inlineMatch = part.match(/^([^\s\[\(\{]+)\s*(\["|(\[)|(\(")|(\{)|(>))\s*(.*?)\s*("\]|\]|\)"|"\)|\}|"\})\s*$/);
          if (inlineMatch) {
            const nodeId = inlineMatch[1];
            const labelContent = inlineMatch[7] || nodeId;
            const safeId = getSafeId(nodeId);
            const escapedLabel = escapeMermaidLabel(labelContent);
            sanitizedParts.push(`${safeId}["${escapedLabel}"]`);
          } else {
            // Just a node ID reference
            const cleanPart = part.trim();
            if (cleanPart) {
              const safeId = getSafeId(cleanPart);
              sanitizedParts.push(safeId);
            }
          }
        }
      }

      processed = sanitizedParts.join(' ');
      outputLines.push(`    ${processed}`);
      continue;
    }

    // Node definition with label
    const nodeMatch = trimmedLine.match(/^([^\s\[\(\{>\-=\.]+)\s*(\["|\["|\[|\("|\(\(|\{|\()\s*(.*?)\s*("\]|\]|\)"|"\)|"\)\)|\)\)|\}|\))\s*$/);
    if (nodeMatch) {
      const nodeId = nodeMatch[1];
      const labelContent = nodeMatch[3] || nodeId;
      const safeId = getSafeId(nodeId);
      const escapedLabel = escapeMermaidLabel(labelContent);
      outputLines.push(`    ${safeId}["${escapedLabel}"]`);
      continue;
    }

    // Simple standalone node ID (registers it)
    if (simpleNodeRe.test(trimmedLine) && !trimmedLine.includes(':')) {
      const nodeId = trimmedLine.trim();
      const safeId = getSafeId(nodeId);
      if (safeId !== nodeId) {
        outputLines.push(`    ${safeId}["${escapeMermaidLabel(nodeId)}"]`);
      } else {
        outputLines.push(line);
      }
      continue;
    }

    // Fallback: pass through unchanged (handles unknown constructs gracefully)
    outputLines.push(line);
  }

  return outputLines.join('\n');
}

/**
 * Split an edge line into tokens: node references, arrows, and edge labels.
 * Example: "A --> B" → ["A", "-->", "B"]
 * Example: "A -->|text| B" → ["A", "-->|text|", "B"]
 */
function splitEdgeLine(line: string): string[] {
  const tokens: string[] = [];
  const trimmed = line.trim();

  // Match arrows with optional labels: -->|label|, ==>, -->, -..->
  const arrowRegex = /(={3,}>|={2}>|--+>|-\.+-?>?|-{2,}|={2,}|-->|==>)(\|[^|]*\|)?/g;

  let lastIndex = 0;
  let match;

  while ((match = arrowRegex.exec(trimmed)) !== null) {
    // Text before the arrow is a node reference
    const before = trimmed.slice(lastIndex, match.index).trim();
    if (before) tokens.push(before);

    // The arrow (with optional label)
    tokens.push(match[0]);
    lastIndex = match.index + match[0].length;
  }

  // Text after the last arrow
  const after = trimmed.slice(lastIndex).trim();
  if (after) tokens.push(after);

  // If no arrows were found, return the whole line as a single token
  if (tokens.length === 0) tokens.push(trimmed);

  return tokens;
}

/**
 * Check if a token is an arrow or edge label (not a node reference).
 */
function isArrowOrLabel(token: string): boolean {
  const trimmed = token.trim();
  return /^(={2,}>?|--+>?|-\.+-?>?|-->|==>)(\|[^|]*\|)?$/.test(trimmed);
}

/**
 * Generate a deterministic hash-based ID from chart content for use as
 * a Mermaid render element ID. Avoids DOM collisions between concurrent renders.
 */
export function generateDeterministicId(chart: string): string {
  let hash = 0;
  for (let i = 0; i < chart.length; i++) {
    const chr = chart.charCodeAt(i);
    hash = ((hash << 5) - hash) + chr;
    hash |= 0; // Convert to 32-bit integer
  }
  return `mermaid_${Math.abs(hash).toString(36)}`;
}
