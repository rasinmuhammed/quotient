import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { NavLinks } from "@/components/nav-links";
import { ServerStatus } from "@/components/server-status";
import "./globals.css";

const sans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Quotient", template: "%s · Quotient" },
  description: "RFQ in, priced quote draft out. Every number traces to a price-list row and a named rule.",
};

function Mark() {
  // A benzene ring: six bonds, three of them double.
  return (
    <svg viewBox="0 0 24 24" className="size-[22px]" aria-hidden>
      <path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4Z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="M12 5.6 17.4 8.7M17.4 15.3 12 18.4M6.6 14.8V9.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity=".55" />
    </svg>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} h-full`}>
      <body className="flex min-h-full flex-col bg-bg text-text">
        <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur-md">
          <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-6 px-5 lg:px-8">
            <Link href="/" className="flex items-center gap-2 text-text">
              <span className="text-accent">
                <Mark />
              </span>
              <span className="text-[15px] font-semibold tracking-[-0.01em]">Quotient</span>
            </Link>
            <NavLinks />
            <div className="ml-auto">
              <ServerStatus />
            </div>
          </div>
        </header>
        <main className="flex flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
