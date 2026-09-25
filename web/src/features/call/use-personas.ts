"use client";

import { useQuery } from "@tanstack/react-query";

import { listPersonas, queryKeys } from "@/lib/api/client";

export function usePersonas() {
  return useQuery({
    queryKey: queryKeys.personas,
    queryFn: listPersonas,
    staleTime: 5 * 60_000, // The roster changes on deploy, not during a session.
  });
}
