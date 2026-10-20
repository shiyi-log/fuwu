<script lang="ts" setup>
import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { AuthenticationLoginExpiredModal } from '@vben/common-ui';
import { BasicLayout, UserDropdown } from '@vben/layouts';
import { UserRoundPen } from '@vben/icons';
import { preferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';

import { useAuthStore } from '#/store';
import LoginForm from '#/views/_core/authentication/login.vue';

const router = useRouter();
const userStore = useUserStore();
const authStore = useAuthStore();
const accessStore = useAccessStore();
const avatar = computed(() => userStore.userInfo?.avatar || preferences.app.defaultAvatar);
const menus = [{ icon: UserRoundPen, text: '个人资料与密码', handler: () => router.push('/profile') }];
const handleLogout = () => authStore.logout(false);
</script>

<template>
  <BasicLayout @clear-preferences-and-logout="handleLogout">
    <template #user-dropdown>
      <UserDropdown :avatar :menus :text="userStore.userInfo?.realName" :description="userStore.userInfo?.username" @logout="handleLogout" @clear-preferences-and-logout="handleLogout" />
    </template>
    <template #extra>
      <AuthenticationLoginExpiredModal v-model:open="accessStore.loginExpired" :avatar><LoginForm /></AuthenticationLoginExpiredModal>
    </template>
  </BasicLayout>
</template>
