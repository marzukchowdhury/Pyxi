/**
 * Real logos for connected accounts. Google's four-colour G is drawn from Google's published
 * sign-in mark; Discord and LinkedIn use the Font Awesome Free brand icons (CC BY 4.0), shown
 * on each brand's own colour.
 */
import type { ReactElement } from "react";
import { faDiscord } from "@fortawesome/free-brands-svg-icons/faDiscord";
import { faLinkedinIn } from "@fortawesome/free-brands-svg-icons/faLinkedinIn";

type FaIcon = { icon: [number, number, unknown, unknown, string | string[]] };

function Fa({ icon, fill }: { icon: FaIcon; fill: string }) {
  const [w, h, , , path] = icon.icon;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="60%" height="60%" aria-hidden="true">
      <path d={Array.isArray(path) ? path.join(" ") : path} fill={fill} />
    </svg>
  );
}

function GoogleG() {
  return (
    <svg viewBox="0 0 48 48" width="62%" height="62%" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

const BRANDS: Record<string, { bg: string; mark: () => ReactElement }> = {
  google: { bg: "#ffffff", mark: () => <GoogleG /> },
  discord: { bg: "#5865F2", mark: () => <Fa icon={faDiscord as unknown as FaIcon} fill="#ffffff" /> },
  linkedin: { bg: "#0A66C2", mark: () => <Fa icon={faLinkedinIn as unknown as FaIcon} fill="#ffffff" /> },
};

/** A brand tile, or null if we don't have that brand's logo. */
export function BrandLogo({ id, size = 30 }: { id: string; size?: number }) {
  const brand = BRANDS[id];
  if (!brand) return null;
  return (
    <span className="brand-logo" style={{ width: size, height: size, background: brand.bg }}>
      {brand.mark()}
    </span>
  );
}
