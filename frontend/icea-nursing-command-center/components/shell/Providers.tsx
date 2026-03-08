"use client";

import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        gcTime: 5 * 60_000,
        retry: 1,
        refetchOnWindowFocus: false
      }
    }
  });
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const [client] = React.useState<QueryClient>(() => makeClient());

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
