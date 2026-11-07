/**
 * "Cheaper elsewhere": a small tag pinned beside the product's price. One line ("$24 less at
 * Best Buy"), the store's price with shipping and when it was checked, and a link to that page.
 * When Pyxi couldn't prove the same item is cheaper somewhere, the tag says that instead.
 * It goes when the page changes, or with the x.
 */
import { useEffect, useState } from "react";
import { bridge, onThisDisplay, toLocal } from "../shared/bridge";
import type { PriceTagInfo } from "../shared/protocol";
import { brain } from "../shared/socket";
import { Interactive } from "./Interactive";

export function PriceTag() {
  const [tag, setTag] = useState<PriceTagInfo | null>(null);
  useEffect(
    () =>
      brain.on((e) => {
        if (e.type === "price_tag") setTag(e.tag);
        else if (e.type === "clear") setTag(null);
      }),
    [],
  );
  useEffect(() => {
    if (!tag || tag.verdict === "cheaper") return;
    const t = setTimeout(() => setTag(null), 20000); // "nothing cheaper" doesn't need to stay
    return () => clearTimeout(t);
  }, [tag]);
  if (!tag) return null;
  if (tag.at && !onThisDisplay(tag.at[0], tag.at[1])) return null;
  const [x, y] = tag.at ? toLocal(tag.at[0], tag.at[1]) : [window.innerWidth - 360, 140];
  const left = Math.max(8, Math.min(x, window.innerWidth - 330));
  const top = Math.max(8, Math.min(y, window.innerHeight - 120));
  return (
    <Interactive className={`price-tag ${tag.verdict}`} style={{ left, top }}>
      <i className="price-tag-dot" />
      <div className="price-tag-body">
        <b>{tag.headline}</b>
        <small>{tag.detail}</small>
        {tag.url && (
          <button className="price-tag-open" onClick={() => bridge.openExternal(tag.url!)}>
            Open {tag.store ?? "the store"}
          </button>
        )}
      </div>
      <button className="price-tag-close" aria-label="Close" onClick={() => setTag(null)}>
        ×
      </button>
    </Interactive>
  );
}
