"use client";

import { RotateCcw } from "lucide-react";
import { useEffect } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";

/** Route-level error boundary — a crash shows a recoverable panel, not a blank page. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Route error:", error);
  }, [error]);

  return (
    <Card className="mx-auto max-w-xl">
      <CardBody className="space-y-4">
        <Alert tone="critical" title="Something broke on this page">
          {error.message || "An unexpected error occurred."}
          {error.digest ? (
            <span className="mt-1 block font-mono text-xs">Digest: {error.digest}</span>
          ) : null}
        </Alert>
        <Button onClick={reset}>
          <RotateCcw aria-hidden />
          Try again
        </Button>
      </CardBody>
    </Card>
  );
}
