/**
 * Hand-typed fixtures for the Settings shell and account screens
 * (Profile, Appearance, Security), typed against the views' props.
 */
import type { ComponentProps } from "react";

import type { LanguageSwitcher } from "@/components/LanguageSwitcher";
import type { AstroliftMyProfile } from "@/graphql/identity/identity.types";
import { DEFAULT_APPEARANCE } from "@/lib/appearance";

import type { useAppearanceSettings } from "./use-appearance-settings";
import type { useProfileIdentity } from "./use-profile-identity";
import type { useProfileTimezone } from "./use-profile-timezone";

const noop = () => {};

export const PROFILE: AstroliftMyProfile = {
  userId: 42,
  username: "leo.mata",
  firstName: "Leo",
  lastName: "Mata",
  email: "leo@example.com",
  lockedFields: [],
  orgAllowsEdit: true,
  timezone: "America/Costa_Rica",
};

export const LONG_PROFILE: AstroliftMyProfile = {
  ...PROFILE,
  username: "maximiliano.alejandro.de.la.fuente-villanueva.contractor@federated-idp",
  firstName: "Maximiliano Alejandro Bartholomew",
  lastName: "de la Fuente-Villanueva y Castellanos-Oyarzún",
  email: "maximiliano.alejandro.delafuente-villanueva@subsidiary.enterprise-holdings.example.com",
  lockedFields: ["first_name", "last_name", "email"],
};

export const IDENTITY: ReturnType<typeof useProfileIdentity> = {
  profile: PROFILE,
  loading: false,
  saving: false,
  save: async () => true,
};

export const LOCALE_SWITCH: ComponentProps<typeof LanguageSwitcher> = {
  locale: "en",
  pending: false,
  onChange: noop,
};

export const TIMEZONE: ReturnType<typeof useProfileTimezone> = {
  timezones: ["America/Costa_Rica", "America/Denver", "America/New_York", "Europe/Madrid", "UTC"],
  browserTz: "America/Denver",
  savedTz: "America/Costa_Rica",
  saving: false,
  save: async () => true,
};

export const APPEARANCE: ReturnType<typeof useAppearanceSettings> = {
  appearance: DEFAULT_APPEARANCE,
  locked: false,
  setAppearance: noop,
  restrictedSettings: "show",
  restrictedSettingsChoice: null,
  restrictedSettingsOrgDefault: "show",
  setRestrictedSettings: noop,
  reset: noop,
  chooseTheme: noop,
  chooseGround: noop,
};
