import { defineOverridesPreferences } from '@vben/preferences';

export const overridesPreferences = defineOverridesPreferences({
  app: {
    accessMode: 'frontend', defaultHomePath: '/projects',
    defaultAvatar: '/fuwu.svg', enableRefreshToken: false,
    enablePreferences: false, enableCopyPreferences: false,
    name: 'fuwu',
  },
  copyright: { enable: false, settingShow: false },
  logo: { source: '/fuwu.svg', sourceDark: '/fuwu.svg' },
  shortcutKeys: { globalPreferences: false, globalLockScreen: false },
  tabbar: { persist: false },
  widget: { notification: false, lockScreen: false, languageToggle: false, timezone: false },
});
