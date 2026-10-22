import type { Metadata } from "next";
import "./globals.css";
import "../styles/controls.css";
import "../styles/chat.css";
import "../styles/markdown.css";
import "../styles/motion.css";

export const metadata: Metadata = {
  title: "OpenPoke Chat",
  description: "Your personal assistant for email, reminders, and the things that matter.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
