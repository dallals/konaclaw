import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import Chat from "./views/Chat";
import Agents from "./views/Agents";
import Permissions from "./views/Permissions";
import Audit from "./views/Audit";
import Shares from "./views/Shares";
import Monitor from "./views/Monitor";
import Connectors from "./views/Connectors";
import Zaps from "./views/Zaps";
import Reminders from "./views/Reminders";
import Skills from "./views/Skills";
import Subagents from "./views/Subagents";
import Portfolio from "./views/Portfolio";
import { WSProvider } from "./ws/WSContext";
import "./index.css";

const qc = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <WSProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />}>
            <Route index element={<Navigate to="/chat" replace />} />
            <Route path="chat" element={<Chat />} />
            <Route path="agents" element={<Agents />} />
            <Route path="connectors" element={<Connectors />} />
            <Route path="connectors/zapier" element={<Zaps />} />
            <Route path="shares" element={<Shares />} />
            <Route path="permissions" element={<Permissions />} />
            <Route path="monitor" element={<Monitor />} />
            <Route path="audit" element={<Audit />} />
            <Route path="reminders" element={<Reminders />} />
            <Route path="skills" element={<Skills />} />
            <Route path="subagents" element={<Subagents />} />
            <Route path="portfolio" element={<Portfolio />} />
          </Route>
        </Routes>
      </BrowserRouter>
      </WSProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
