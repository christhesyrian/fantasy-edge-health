import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Saira_Condensed } from "next/font/google";

import { Providers } from "@/components/Providers";

import "./globals.css";

/**
 * Type system.
 *
 * Saira Condensed carries the broadcast lower-third voice: condensed, engineered,
 * built to fit a lot of label into a narrow rail. IBM Plex Sans reads cleanly at
 * small sizes without the anonymity of a system stack. IBM Plex Mono handles every
 * number, because tabular figures keep a dense column from jittering as values
 * change mid-draft.
 */
const saira = Saira_Condensed({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-saira",
  display: "swap",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Fantasy Health Edge — Draft War Room",
  description:
    "Injury-adjusted fantasy football draft intelligence. Every score decomposable, every number sourced.",
};

export const viewport: Viewport = {
  themeColor: "#0a0908",
  colorScheme: "dark light",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${saira.variable} ${plexSans.variable} ${plexMono.variable} antialiased`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
