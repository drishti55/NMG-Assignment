import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Shell } from "./App";
import "./index.css";
import { ModeProvider } from "./lib/mode";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ModeProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </ModeProvider>
  </React.StrictMode>,
);
