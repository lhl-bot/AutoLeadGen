import type { Metadata } from "next";
import "./globals.css";
import { LanguageProvider } from "@/lib/i18n";
import DynamicLang from "@/components/DynamicLang";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "AutoLeadGen | AI B2B Sales Agent",
  description: "Evidence-backed B2B research, reviewed email outreach, reply handling, and production safety controls.",
  other: {
    google: "notranslate",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="dark font-sans notranslate" translate="no">
      <body className="min-h-screen bg-background text-foreground antialiased notranslate" translate="no">
        <LanguageProvider>
          <DynamicLang />
          {children}
          <Toaster richColors closeButton position="top-right" />
        </LanguageProvider>
      </body>
    </html>
  );
}
