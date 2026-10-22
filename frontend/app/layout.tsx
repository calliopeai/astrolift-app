import type { Metadata } from "next";
import localFont from "next/font/local";
import { ApolloWrapper } from "@/lib/apollo";
import { AppearanceProvider } from "@/providers/AppearanceProvider";
import { TooltipProvider } from "@/components/screens/shell/root-providers";
import { AppearanceBootScript } from "@/components/screens/shell/AppearanceBootScript";
import { ConfirmProvider } from "@/hooks/use-confirm";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/Toaster";
import { TimezoneDetector } from "@/providers/TimezoneDetector";
import { getDirection } from "@/lib/i18n/direction";
import "./globals.css";

const dmSans = localFont({
  src: "./fonts/dm-sans/DMSans-VF.ttf",
  weight: "100 1000",
  style: "normal",
  display: "swap",
  variable: "--font-dm-sans",
});

const plexSans = localFont({
  src: "./fonts/ibm-plex-sans/IBMPlexSans-VF.ttf",
  weight: "300 700",
  style: "normal",
  display: "swap",
  variable: "--font-plex-sans",
});

const plexMono = localFont({
  src: [
    {
      path: "./fonts/ibm-plex-mono/IBMPlexMono-Regular.ttf",
      weight: "400",
      style: "normal",
    },
    {
      path: "./fonts/ibm-plex-mono/IBMPlexMono-Medium.ttf",
      weight: "500",
      style: "normal",
    },
    {
      path: "./fonts/ibm-plex-mono/IBMPlexMono-SemiBold.ttf",
      weight: "600",
      style: "normal",
    },
  ],
  display: "swap",
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
      <head>
        <AppearanceBootScript />
      </head>
      <body className={`${plexSans.variable} ${plexMono.variable} ${dmSans.variable} antialiased`}>
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
                <AppearanceProvider>
                  <ConfirmProvider>{children}</ConfirmProvider>
                </AppearanceProvider>
              </TooltipProvider>
            </ApolloWrapper>
          </NextIntlClientProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
