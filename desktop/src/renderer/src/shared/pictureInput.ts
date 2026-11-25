/**
 * A picture put in a chat box: pasted (a screenshot, a copied image) or an image file dropped
 * on it. It is made small here before it is sent (a 20-megapixel photo is far more than the
 * model can use, and far too big a message), keeping a see-through background see-through.
 * It goes to the brain with the typed words; nothing is sent until they press Enter.
 */
import { type ClipboardEvent, type DragEvent, useCallback, useState } from "react";

export interface ChatPicture {
  /** What is sent: a data URL (JPEG, or PNG when the picture has a see-through background). */
  data: string;
  name: string;
}

export const PICTURE_SIDE = 1600; // the longest side that is sent
export const MAX_PICTURE_BYTES = 60 * 1024 * 1024; // a file bigger than this is not opened at all
/** What a message with a picture and no words asks for. */
export const PICTURE_ALONE = "Make a 3D model of this picture.";

interface FileLike { type: string; size: number; name?: string }
interface ItemLike { kind: string; type: string; getAsFile(): FileLike | null }
interface TransferLike { items?: ArrayLike<ItemLike> | null; files?: ArrayLike<FileLike> | null }

const isPicture = (f: FileLike | null | undefined): f is FileLike =>
  !!f && /^image\/(png|jpe?g|webp|gif|bmp|avif)$/i.test(f.type) && f.size > 0;

/** The first picture in what was pasted or dropped, or null if there is none. */
export function pictureIn<T extends FileLike = File>(transfer: TransferLike | null | undefined): T | null {
  if (!transfer) return null;
  for (const item of Array.from(transfer.items ?? [])) {
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (isPicture(f)) return f as T;
  }
  for (const f of Array.from(transfer.files ?? [])) if (isPicture(f)) return f as T;
  return null;
}

/** The size a picture is sent at: the same shape, its longest side `max` at most, never bigger than it was. */
export function fitWithin(width: number, height: number, max = PICTURE_SIDE): [number, number] {
  const k = Math.min(1, max / Math.max(width, height, 1));
  return [Math.max(1, Math.round(width * k)), Math.max(1, Math.round(height * k))];
}

/** Does any pixel let the background through? (RGBA bytes; a few stray soft pixels don't count.) */
export function seeThrough(rgba: Uint8ClampedArray | Uint8Array): boolean {
  let clear = 0;
  const need = Math.max(8, rgba.length / 4 / 500);
  for (let i = 3; i < rgba.length; i += 4) if (rgba[i] < 200 && ++clear >= need) return true;
  return false;
}

/** Open a picture file and make the small copy that is sent. Null if it can't be opened. */
export async function readPicture(file: Blob & { name?: string }): Promise<ChatPicture | null> {
  if (file.size > MAX_PICTURE_BYTES) return null;
  try {
    const bitmap = await createImageBitmap(file);
    const [w, h] = fitWithin(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close();
    const clear = file.type !== "image/jpeg" && seeThrough(ctx.getImageData(0, 0, w, h).data);
    const data = clear ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.9);
    return { data, name: file.name || "Pasted picture" };
  } catch {
    return null;
  }
}

/**
 * A chat box that takes a picture: spread `handlers` on the form (or the input), show
 * `picture` as a chip, and send it with the words.
 */
export function useChatPicture() {
  const [picture, setPicture] = useState<ChatPicture | null>(null);
  const [problem, setProblem] = useState("");
  const [over, setOver] = useState(false);
  const take = useCallback(async (file: File | null) => {
    if (!file) return false;
    const got = await readPicture(file);
    setPicture(got);
    setProblem(got ? "" : "That picture wouldn't open. A JPG or a PNG works.");
    return true;
  }, []);
  const onPaste = useCallback((e: ClipboardEvent) => {
    const file = pictureIn<File>(e.clipboardData);
    if (!file) return; // words: pasted into the box as usual
    e.preventDefault();
    void take(file);
  }, [take]);
  const onDragOver = useCallback((e: DragEvent) => {
    if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setOver(true);
  }, []);
  const onDragLeave = useCallback(() => setOver(false), []);
  const onDrop = useCallback((e: DragEvent) => {
    setOver(false);
    if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
    e.preventDefault(); // never let a dropped file open in place of the app
    const file = pictureIn<File>(e.dataTransfer);
    if (file) void take(file);
    else setProblem("I can only take a picture here: a JPG or a PNG.");
  }, [take]);
  const clear = useCallback(() => { setPicture(null); setProblem(""); }, []);
  return { picture, problem, over, clear, handlers: { onPaste, onDragOver, onDragLeave, onDrop } };
}
