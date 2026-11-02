import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { BackendGate } from "./components/BackendGate";
import "./styles/globals.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BackendGate>
      <App />
    </BackendGate>
  </React.StrictMode>
);
