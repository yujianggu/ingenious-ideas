import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./style.css";
import { DirtyProvider } from "./dirty";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DirtyProvider>
      <App />
    </DirtyProvider>
  </React.StrictMode>,
);
