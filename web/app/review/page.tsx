import type { Metadata } from "next";
import { Suspense } from "react";
import { Review } from "@/components/review/review";

export const metadata: Metadata = { title: "Review" };

export default function ReviewPage() {
  // The draft lives in the browser (search params name it), so this route renders on the client.
  return (
    <Suspense fallback={null}>
      <Review />
    </Suspense>
  );
}
