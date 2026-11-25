/**
 * The photo a 3D model was built from, kept beside the model (ObjectPanel). Only what Pyxi
 * itself makes is ever shown: a small JPEG or PNG held in the page as a data URL. Anything
 * else in that field (a web address, an SVG, something huge) is not a photo and is dropped.
 */
export const MAX_PHOTO_CHARS = 400_000;

export function safePhoto(photo: string | null | undefined): string | null {
  if (typeof photo !== "string" || photo.length > MAX_PHOTO_CHARS) return null;
  return /^data:image\/(?:jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(photo) ? photo : null;
}

export { photoAngle } from "../scene/camera";
