import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProviderBoundary } from "./auth/AuthProviderBoundary";
import "./styles/app.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AuthProviderBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AuthProviderBoundary>
  </React.StrictMode>,
);
