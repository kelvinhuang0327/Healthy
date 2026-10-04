import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Healthy",
  description:
    "Private health tracking for measurements, symptoms, habits, and reminders. Health summaries are informational, not diagnoses.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
