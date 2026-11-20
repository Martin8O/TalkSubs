import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { apply, remembered } from "./state/prefs";
import "./styles.css";

apply(remembered()); // the first paint, before the settings arrive from the app

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
