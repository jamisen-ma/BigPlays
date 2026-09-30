import Ionicons from '@expo/vector-icons/Ionicons'
import { Tabs } from 'expo-router'
import type { ColorValue } from 'react-native'

import { C } from '@/lib/theme'

type IconName = keyof typeof Ionicons.glyphMap

const icon = (name: IconName) => ({ color, size }: { color: ColorValue; size: number }) =>
  <Ionicons name={name} color={color as string} size={size} />

export default function TabsLayout() {
  return (
    <Tabs screenOptions={{
      headerShown: false,
      tabBarActiveTintColor: C.accent,
      tabBarInactiveTintColor: C.muted,
      tabBarStyle: { backgroundColor: C.bg, borderTopColor: C.line },
      sceneStyle: { backgroundColor: C.bg },
    }}>
      <Tabs.Screen name="index" options={{ title: 'Scores', tabBarIcon: icon('stats-chart') }} />
      <Tabs.Screen name="clips" options={{ title: 'Clips', tabBarIcon: icon('film') }} />
      <Tabs.Screen name="settings" options={{ title: 'Settings', tabBarIcon: icon('settings-sharp') }} />
    </Tabs>
  )
}
