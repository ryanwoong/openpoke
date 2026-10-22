import type { Metadata } from "next";
import "./globals.css";
import "../styles/controls.css";
import "../styles/chat.css";
import "../styles/markdown.css";

export const metadata: Metadata = {
  title: "OpenPoke Chat",
  description: "A beautiful OpenRouter chat powered by Vercel AI SDK",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-gray-900 antialiased">{children}</body>
    </html>
  );
}
