import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import { createRoot } from "react-dom/client";
import "./panel.css";
import { Panel } from "./Panel";

if (!window.pyxiBridge) document.documentElement.classList.add("web-preview");
createRoot(document.getElementById("root")!).render(<Panel />);
