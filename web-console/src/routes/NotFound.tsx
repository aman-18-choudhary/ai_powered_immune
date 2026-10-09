import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto grid min-h-[60dvh] max-w-md place-items-center px-4 text-center">
      <div>
        <Compass className="mx-auto h-10 w-10 text-subtle" aria-hidden />
        <h1 className="mt-4 text-2xl font-semibold">Page not found</h1>
        <p className="mt-2 text-sm text-muted">That route does not exist in this console.</p>
        <Link to="/" className="mt-5 inline-block">
          <Button variant="primary">Back to the live interception</Button>
        </Link>
      </div>
    </div>
  );
}
