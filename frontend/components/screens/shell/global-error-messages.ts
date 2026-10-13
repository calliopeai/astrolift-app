"use client";

import * as React from "react";

import { defaultLocale, type Locale, locales } from "@/i18n/config";

interface GlobalErrorCopy {
  errorScreens: { title: string; critical: string; errorId: string; tryAgain: string };
}

/**
 * The hard-crash screen's copy in every locale (#2381). app/global-error.tsx
 * replaces the root layout, so the request's catalog and its intl provider
 * are gone with it, and shipping every catalog to the browser for four
 * strings would weigh down the whole client bundle. These mirror
 * `errorScreens` in each catalog; global-error-messages.test.ts fails the
 * moment they differ.
 */
export const GLOBAL_ERROR_MESSAGES: Record<Locale, GlobalErrorCopy> = {
  en: {
    errorScreens: {
      title: "Something went wrong",
      critical: "A critical error occurred. Please refresh the page.",
      errorId: "Error ID: {digest}",
      tryAgain: "Try again",
    },
  },
  es: {
    errorScreens: {
      title: "Algo salió mal",
      critical: "Se produjo un error crítico. Recarga la página.",
      errorId: "ID del error: {digest}",
      tryAgain: "Reintentar",
    },
  },
  fr: {
    errorScreens: {
      title: "Une erreur s’est produite",
      critical: "Une erreur critique s’est produite. Veuillez actualiser la page.",
      errorId: "ID de l’erreur : {digest}",
      tryAgain: "Réessayer",
    },
  },
  de: {
    errorScreens: {
      title: "Etwas ist schiefgelaufen",
      critical: "Ein kritischer Fehler ist aufgetreten. Bitte laden Sie die Seite neu.",
      errorId: "Fehler-ID: {digest}",
      tryAgain: "Erneut versuchen",
    },
  },
  ja: {
    errorScreens: {
      title: "問題が発生しました",
      critical: "重大なエラーが発生しました。ページを再読み込みしてください。",
      errorId: "エラー ID: {digest}",
      tryAgain: "再試行",
    },
  },
  ko: {
    errorScreens: {
      title: "문제가 발생했습니다",
      critical: "심각한 오류가 발생했습니다. 페이지를 새로 고치세요.",
      errorId: "오류 ID: {digest}",
      tryAgain: "다시 시도",
    },
  },
  "zh-Hans": {
    errorScreens: {
      title: "出错了",
      critical: "发生严重错误，请刷新页面。",
      errorId: "错误 ID：{digest}",
      tryAgain: "重试",
    },
  },
  "pt-BR": {
    errorScreens: {
      title: "Algo deu errado",
      critical: "Ocorreu um erro crítico. Atualize a página.",
      errorId: "ID do erro: {digest}",
      tryAgain: "Tentar novamente",
    },
  },
};

/** The `locale` cookie the language switch writes, when it names a locale we ship. */
export function cookieLocale(cookie: string): Locale {
  const raw = cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("locale="))
    ?.slice("locale=".length);
  return locales.includes(raw as Locale) ? (raw as Locale) : defaultLocale;
}

const subscribe = () => () => {};

/**
 * The viewer's locale without the request: the server and the first client
 * render use the default, then the cookie's (no hydration mismatch).
 */
export function useCookieLocale(): Locale {
  return React.useSyncExternalStore(
    subscribe,
    () => cookieLocale(document.cookie),
    () => defaultLocale
  );
}
