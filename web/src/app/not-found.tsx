import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { FileQuestion } from "lucide-react";

export default function NotFound() {
  return (
    <Card className="mx-auto max-w-xl">
      <CardBody>
        <EmptyState
          icon={FileQuestion}
          title="Page not found"
          description="That route does not exist in this app."
          action={
            <Button asChild>
              <Link href="/">Back to the live line</Link>
            </Button>
          }
        />
      </CardBody>
    </Card>
  );
}
