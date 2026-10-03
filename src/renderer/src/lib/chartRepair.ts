/** Algorithmic fixes for the near-miss JSON a model writes in a ```chart block, applied only after a strict parse fails. */
export function repairJson(source: string): string {
  let s = source.trim().replace(/^```(?:json|chart)?\s*|\s*```$/g, '')
  s = s.replace(/[“”]/g, '"').replace(/[‘’]/g, "'")
  // drop line comments and trailing commas outside strings
  let out = ''
  let q = ''
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (q) {
      out += c
      if (c === '\\') out += s[++i] ?? ''
      else if (c === q) q = ''
    } else if (c === '"' || c === "'") {
      q = c
      out += '"'
    } else if (c === '/' && s[i + 1] === '/') {
      while (i < s.length && s[i] !== '\n') i++
    } else if (c === ',' && /^\s*[}\]]/.test(s.slice(i + 1))) {
      // trailing comma
    } else out += c
  }
  return out
}

/** JSON.parse, falling back to repairJson; throws the original error if neither parses. */
export function parseJsonLoose(source: string): unknown {
  try {
    return JSON.parse(source)
  } catch (e) {
    try { return JSON.parse(repairJson(source)) } catch { throw e }
  }
}
