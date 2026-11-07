// Settings: apps Pyxi never looks at. The built-in list (password managers, banking, sign-in,
// the lock screen, UAC) always applies; testers add their own here (pyxi/screen/private_apps.py).
import { useEffect, useState } from "react";
import { brain } from "../shared/socket";

export function PrivateApps() {
  const [apps, setApps] = useState<string[] | null>(null);
  const [builtIn, setBuiltIn] = useState<string[]>([]);
  const [adding, setAdding] = useState("");
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type === "private_apps") {
        setApps(e.apps);
        setBuiltIn(e.built_in);
      }
    });
    brain.send({ type: "private_apps_get" });
    return off;
  }, []);
  if (!apps) return null;
  const save = (next: string[]) => {
    setApps(next);
    brain.send({ type: "private_apps_save", apps: next });
  };
  const add = () => {
    const name = adding.trim().replace(/^"|"$/g, "");
    if (name.length >= 2 && !apps.some((a) => a.toLowerCase() === name.toLowerCase())) save([...apps, name]);
    setAdding("");
  };
  return (
    <div className="file-access private-apps">
      <p className="muted">Pyxi never looks at these. When one is in front it says it can't see it; anywhere else on screen it's blacked out before anything reads the screenshot.</p>
      <ul className="folders">
        {builtIn.map((b) => (
          <li key={b} className="muted">{b}</li>
        ))}
        {apps.map((a) => (
          <li key={a}>
            <code title={a}>{a}</code>
            <button className="ghost" aria-label={`Let Pyxi see ${a} again`} onClick={() => save(apps.filter((x) => x !== a))}>Remove</button>
          </li>
        ))}
      </ul>
      <form className="folder-add" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input value={adding} placeholder="Add an app or window name, e.g. Quicken" onChange={(e) => setAdding(e.target.value)} />
        <button type="submit" disabled={adding.trim().length < 2}>Add</button>
      </form>
    </div>
  );
}
