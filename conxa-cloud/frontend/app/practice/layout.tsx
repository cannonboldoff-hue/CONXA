import type { Metadata } from "next"
import { PracticeShell } from "./_shell"

// A pretend company used by the Build Studio tour. Kept out of search engines; nothing here is real.
export const metadata: Metadata = { title: "Northwind Practice", robots: { index: false, follow: false } }

export default function PracticeLayout({ children }: { children: React.ReactNode }) {
  return <PracticeShell>{children}</PracticeShell>
}
