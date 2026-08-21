import type { Metadata } from "next";
import { DM_Sans, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { ApolloWrapper } from "@/lib/apollo";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConfirmProvider } from "@/hooks/use-confirm";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/Toaster";
import { TimezoneDetector } from "@/components/TimezoneDetector";
import { getDirection } from "@/lib/i18n/direction";
import "./globals.css";

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm-sans",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-plex-sans",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: {
    default: "Astrolift",
    template: "%s · Astrolift",
  },
  description: "Astrolift platform dashboard",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  const messages = await getMessages();
  const direction = getDirection(locale);

  return (
    <html lang={locale} dir={direction} suppressHydrationWarning>
      <body
        className={`${plexSans.variable} ${plexMono.variable} ${dmSans.variable} antialiased`}
      >
        <TimezoneDetector />
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <Toaster />
          <NextIntlClientProvider locale={locale} messages={messages}>
            <ApolloWrapper>
              <TooltipProvider>
                <ConfirmProvider>{children}</ConfirmProvider>
              </TooltipProvider>
            </ApolloWrapper>
          </NextIntlClientProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
