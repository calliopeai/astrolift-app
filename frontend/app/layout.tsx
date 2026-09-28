import type { Metadata } from "next";
import { DM_Sans, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { ApolloWrapper } from "@/lib/apollo";
import { AppearanceProvider } from "@/providers/AppearanceProvider";
import { STORAGE_KEY } from "@/lib/appearance";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConfirmProvider } from "@/hooks/use-confirm";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/Toaster";
import { TimezoneDetector } from "@/providers/TimezoneDetector";
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
      <head>
        {/* Stamp the appearance axes before first paint. Without this the
         * page renders one theme and then swaps once React hydrates, which
         * reads as a flash. Mirrors what next-themes does for light/dark. */}
        <script
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var p=JSON.parse(localStorage.getItem(${JSON.stringify(
              STORAGE_KEY,
            )})||"{}");var d=document.documentElement;
d.dataset.ground=["black","charcoal","emerald","paper","mist"].indexOf(p.ground)>-1?p.ground:"black";
d.dataset.accent=["green","copper","ice","periwinkle","amber"].indexOf(p.accent)>-1?p.accent:"green";
d.dataset.density=p.density==="cards"?"cards":"compact";
d.style.setProperty("--radius",([0,2,4,10].indexOf(p.corners)>-1?p.corners:2)+"px");}catch(e){}})();`,
          }}
        />
      </head>
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
