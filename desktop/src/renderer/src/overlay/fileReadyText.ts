// Words for the new-file card (FileReady.tsx): what kind of file, how big, and the title.

// Format badges: a colour and a short label per kind of file.
const KINDS: [RegExp, string, string, string][] = [
  [/\.pdf$/i, "PDF", "#ff6b6b", "PDF"],
  [/\.docx?$/i, "DOC", "#5b9dff", "Word document"],
  [/\.(xlsx?|xlsm|csv)$/i, "XLS", "#3fcf8e", "spreadsheet"],
  [/\.pptx?$/i, "PPT", "#ff9f5a", "slide deck"],
  [/\.(png|jpe?g|webp|gif|bmp|tiff?|heic)$/i, "IMG", "#c79bff", "image"],
  [/\.(md|txt)$/i, "TXT", "#a7b4c8", "text file"],
];

export function kindOf(name: string): { badge: string; color: string; noun: string } {
  for (const [re, badge, color, noun] of KINDS) if (re.test(name)) return { badge, color, noun };
  const ext = name.split(".").pop()?.slice(0, 4).toUpperCase() || "FILE";
  return { badge: ext, color: "#9ecbff", noun: "file" };
}

export function sizeText(n: number): string {
  if (n < 1024) return `${n} bytes`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** "Your PDF is ready" / "3 PDFs are ready" / "Your images are ready". */
export function readyTitle(files: string[]): string {
  const kinds = new Set(files.map((f) => kindOf(f).noun));
  const noun = kinds.size === 1 ? [...kinds][0] : "file";
  if (files.length === 1) return `Your ${noun} is ready`;
  const plural = noun === "PDF" ? "PDFs" : noun === "file" ? "files" : `${noun}s`;
  return `${files.length} ${plural} are ready`;
}

/** The summary minus what the card already shows (where it is). */
export function cleanBody(body: string): string {
  return body.replace(/\s*(It's|They're) in [A-Z]:\\[^\n]*?\.(?=\s|$)/g, "").trim();
}
