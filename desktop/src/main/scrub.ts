// Log lines without personal data (the same idea as pyxi/logscrub.py, for the desktop app's own
// logs such as .pyxi/updates.log): emails, phone numbers, the user's name in paths, and tokens.
const EMAIL = /[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const PHONE = /(?<![\w.:/-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{3,4}[ .-]\d{3,4}(?![\w.:/-])/g;
const HOME = /\b([a-z]:[\\/]+users[\\/]+)[^\\/\s"'<>|:*?]+/gi;
const TOKEN = /\b(sk-(?:or-v1-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|pyxi_[0-9a-f]{40,}|eyJ[\w-]{15,}\.[\w-]{15,}\.[\w-]{10,})/g;
const BEARER = /\b(bearer|token|key)([\s:="']+)[A-Za-z0-9._~+/=-]{16,}/gi;

export function scrubLine(line: string, secrets: string[] = []): string {
  let out = line;
  for (const s of secrets) if (s && s.length >= 16) out = out.split(s).join("[hidden]");
  return out
    .replace(TOKEN, "[hidden]")
    .replace(BEARER, "$1$2[hidden]")
    .replace(EMAIL, "<email>")
    .replace(PHONE, "<phone>")
    .replace(HOME, "$1<user>");
}
