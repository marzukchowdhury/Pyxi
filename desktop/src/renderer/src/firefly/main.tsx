import { createRoot } from "react-dom/client";
import { Firefly } from "./Firefly";

document.documentElement.classList.add("firefly-page");
createRoot(document.getElementById("root")!).render(<Firefly />);
