import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/fraunces/600.css";
import "@fontsource/fraunces/500-italic.css";
import { createRoot } from "react-dom/client";
import "./overlay.css";
import { Overlay } from "./Overlay";

createRoot(document.getElementById("root")!).render(<Overlay />);
