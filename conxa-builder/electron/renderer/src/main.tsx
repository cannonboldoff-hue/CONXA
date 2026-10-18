import React from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/sonner";
import { App } from "./App";
import "./globals.css";

// Force dark class — studio is always dark
document.documentElement.classList.add("dark");

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

// A data router (not <HashRouter>) so a page can block navigation — Human Edit asks before leaving
// with an unsaved change. One splat route: <App> keeps its own <Routes>.
const router = createHashRouter([
  {
    path: "*",
    element: (
      <>
        <App />
        <Toaster />
      </>
    ),
  },
]);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
