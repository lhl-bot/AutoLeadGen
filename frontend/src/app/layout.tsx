import type { Metadata } from "next";
import "./globals.css";
import { LanguageProvider } from "@/lib/i18n";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "AutoLeadGen | AI B2B Sales Agent",
  description: "Automate your outbound sales with AI-driven research and omnichannel reach.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark font-sans">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <LanguageProvider>
          {children}
          <Toaster richColors closeButton position="top-right" />
        </LanguageProvider>
      </body>
    </html>
  );
}
