import { useEffect, useState } from "react";
import { brain } from "../shared/socket";

/** "Check prices while I shop": off until they turn it on. */
export function PriceChecks() {
  const [auto, setAuto] = useState<boolean | null>(null);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "shopping") setAuto(e.auto);
    });
    brain.send({ type: "shopping_get" });
    return off;
  }, []);
  if (auto === null) return null;
  return (
    <div className="file-access">
      <label>
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => {
            setAuto(e.target.checked);
            brain.send({ type: "shopping_save", auto: e.target.checked });
          }}
        />{" "}
        Check prices while I shop
      </label>
      <p className="muted">
        On a store's product page, Scout quietly looks for the exact same item at other stores and pins a tag by the price when it's cheaper somewhere. It reads store pages in the background, never logs in and never touches your mouse. You can always ask "is this cheaper anywhere?" with this off.
      </p>
    </div>
  );
}
