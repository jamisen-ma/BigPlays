import { DarkTheme, Stack, ThemeProvider, type Theme } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import * as SystemUI from 'expo-system-ui'
import { useEffect } from 'react'

import { ApiProvider } from '@/lib/ApiContext'
import { C } from '@/lib/theme'

const theme: Theme = {
  ...DarkTheme,
  colors: { ...DarkTheme.colors, primary: C.accent, background: C.bg, card: C.bg, text: C.text, border: C.line, notification: C.live },
}

export default function RootLayout() {
  useEffect(() => { SystemUI.setBackgroundColorAsync(C.bg).catch(() => {}) }, [])
  return (
    <ApiProvider>
      <ThemeProvider value={theme}>
        <StatusBar style="light" />
        <Stack screenOptions={{
          headerStyle: { backgroundColor: C.bg },
          headerTintColor: C.accent,
          headerTitleStyle: { color: C.text, fontWeight: '700' },
          contentStyle: { backgroundColor: C.bg },
          headerBackTitle: 'Scores',
        }}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="game/[league]/[id]" options={{ title: 'Game' }} />
          <Stack.Screen name="player" options={{ presentation: 'fullScreenModal', headerShown: false, animation: 'fade' }} />
        </Stack>
      </ThemeProvider>
    </ApiProvider>
  )
}
