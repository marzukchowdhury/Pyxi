// Settings: "Connect your browser". A pairing code for Pyxi's browser extension, which browsers
// are paired and connected right now, and the sites where Pyxi may click and type
// (pyxi/browserlink.py). The code is shown here only, works once and lasts five minutes.
import { useEffect, useRef, useState } from "react";
import { brain } from "../shared/socket";
import type { BrowserState } from "../shared/protocol";

const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export function BrowserConnect() {
  const [state, setState] = useState<BrowserState | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const paired = useRef(0);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type !== "browser") return;
      setState({ connected: e.connected, browsers: e.browsers, sites: e.sites });
      if (e.code) setCode(e.code);
      else if (e.browsers.length > paired.current) setCode(null); // the code was used: a browser paired
      paired.current = e.browsers.length;
    });
    brain.send({ type: "browser_get" });
    return off;
  }, []);
  if (!state) return null;
  return (
    <div className="file-access browser-connect">
      <ul className="folders">
        {state.browsers.map((b) => (
          <li key={b.id}>
            <span><i className={`link-dot${b.connected ? " on" : ""}`} />{b.browser}: {b.connected ? "connected" : "not open right now"}</span>
            <button className="ghost" aria-label={`Disconnect ${b.browser}`} onClick={() => brain.send({ type: "browser_forget", id: b.id })}>Disconnect</button>
          </li>
        ))}
        {!state.browsers.length && <li className="muted">No browser connected yet.</li>}
      </ul>
      {code ? (
        <p>In your browser, click the Pyxi icon and type this code: <b className="pair-code">{code}</b><br /><span className="muted">It works once, for 5 minutes.</span></p>
      ) : (
        <button onClick={() => brain.send({ type: "browser_pair" })}>Connect a browser</button>
      )}
      {state.sites.length > 0 && (
        <ul className="folders">
          {state.sites.map((s) => (
            <li key={s.host}>
              <span title="Pyxi may click and type on this site without asking first">{s.host}, until {day(s.expires)}</span>
              <button className="ghost" aria-label={`Stop Pyxi acting on ${s.host}`} onClick={() => brain.send({ type: "browser_site_revoke", host: s.host })}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted">With the Pyxi extension in Chrome, Edge or Firefox, Pyxi can read and work in your own tabs, signed in as you, without taking your mouse. It asks the first time it acts on a site and before anything is sent, submitted, bought, deleted or posted. It never types passwords or card numbers, and Esc in the tab stops it.</p>
    </div>
  );
}
