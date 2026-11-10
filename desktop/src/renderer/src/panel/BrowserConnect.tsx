// Settings: "Connect your browser". Getting Pyxi's browser extension in ("Add to Firefox", or
// "Set it up for me" in Chrome and Edge, where Pyxi opens the page and points at each step),
// a pairing code, which browsers are paired and connected right now, and the sites where Pyxi
// may click and type (pyxi/browserlink.py, pyxi/companion/browsersetup.py). The code is shown
// here only, works once and lasts five minutes.
import { useEffect, useRef, useState } from "react";
import { brain } from "../shared/socket";
import type { BrowserSetup, BrowserState } from "../shared/protocol";

const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export function BrowserConnect() {
  const [state, setState] = useState<BrowserState | null>(null);
  const [setup, setSetup] = useState<BrowserSetup | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const paired = useRef(0);
  useEffect(() => {
    const off = brain.on((e) => {
      if (e.type !== "browser") return;
      setState({ connected: e.connected, browsers: e.browsers, sites: e.sites });
      if (e.setup) setSetup(e.setup);
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
      {!state.connected && <SetUp setup={setup} />}
      {code ? (
        <p>In your browser, click the Pyxi icon and type this code: <b className="pair-code">{code}</b><br /><span className="muted">It works once, for 5 minutes.</span></p>
      ) : (
        <button className={state.connected || !setup?.browser ? "primary" : "ghost"} onClick={() => brain.send({ type: "browser_pair" })}>{state.browsers.length || !setup?.browser ? "Connect a browser" : "I already have the extension: show me a code"}</button>
      )}
      {state.sites.length > 0 && (
        <ul className="folders">
          {state.sites.map((s) => (
            <li key={s.host}>
              <span title="You chose Always allow on this site: Pyxi may click and type here without asking first">{s.host}, until {day(s.expires)}</span>
              <button className="ghost" aria-label={`Stop Pyxi acting on ${s.host}`} onClick={() => brain.send({ type: "browser_site_revoke", host: s.host })}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted">With the Pyxi extension in Chrome, Edge or Firefox, Pyxi can read and work in your own tabs, signed in as you, without taking your mouse. It asks before it acts on a site (Always allow on this site keeps one for 30 days) and before anything is sent, submitted, bought, deleted or posted. It never types passwords or card numbers, and Esc in the tab stops it.</p>
    </div>
  );
}

/** No browser is connected: offer to put the extension into the browser that is open. */
function SetUp({ setup }: { setup: BrowserSetup | null }) {
  if (!setup?.browser) return <p className="muted">Open Chrome, Edge or Firefox and come back here: Pyxi can set the extension up for you.</p>;
  const start = () => brain.send({ type: "browser_setup", browser: setup.key });
  const firefox = setup.kind === "firefox";
  return (
    <div className="browser-setup">
      {setup.running ? (
        <p>Setting it up in {setup.browser}: follow the pointer on your screen.</p>
      ) : (
        <button className="primary" onClick={start}>{firefox && setup.addon ? "Add to Firefox" : `Set it up for me in ${setup.browser}`}</button>
      )}
      {firefox && setup.addon && <p className="muted">Firefox opens and asks if you want to add it. After that Pyxi shows a code to type into the add-on.</p>}
      {firefox && setup.addon === false && <p className="muted">The Firefox add-on that stays installed isn't published yet. Pyxi can load it for now, until Firefox closes.</p>}
      {!firefox && <p className="muted">Pyxi opens {setup.browser}'s extensions page, copies the folder's address for you and points at each step: Developer mode, Load unpacked, paste. {setup.notice}</p>}
      {!firefox && setup.checked === false && <p className="muted">These steps were checked in Chrome and Edge. {setup.browser} uses the same page, so they should match.</p>}
      {!!setup.steps?.length && (
        <details>
          <summary>Or do it by hand</summary>
          <ol>{setup.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        </details>
      )}
    </div>
  );
}
