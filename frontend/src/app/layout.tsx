import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PDF Extractor AI",
  description:
    "Extract everything from any PDF with zero hallucinations. Pixel-perfect layout view, Markdown export, editable Word export.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        {children}
      </body>
    </html>
  );
}
