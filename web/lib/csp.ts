/** Restrictive CSP for the browser reader. XSS here is a T1 bypass. */

export function buildCsp(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    "style-src 'self'",
    "img-src 'self'",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function cspIsRestrictive(header: string): boolean {
  const lower = header.toLowerCase();
  return (
    /default-src\s+'self'/.test(lower) &&
    !lower.includes("unsafe-inline") &&
    !lower.includes("unsafe-eval") &&
    !/script-src[^;]*https?:\/\//i.test(header)
  );
}
