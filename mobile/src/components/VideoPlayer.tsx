import { useEvent } from 'expo'
import { useVideoPlayer, VideoView } from 'expo-video'
import { StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import { C } from '@/lib/theme'

/**
 * expo-video player for one URL. Mount it with `key={url}` so switching clips gets a fresh player.
 * Autoplays; native controls + fullscreen + PiP.
 */
export function VideoPlayer({ url, style, fill }: { url: string; style?: StyleProp<ViewStyle>; fill?: boolean }) {
  const player = useVideoPlayer(url, p => { p.loop = false; p.play() })
  const { status, error } = useEvent(player, 'statusChange', { status: player.status, error: undefined })
  return (
    <View style={[fill ? StyleSheet.absoluteFill : s.box, style]}>
      <VideoView player={player} style={StyleSheet.absoluteFill} nativeControls contentFit="contain"
        allowsPictureInPicture fullscreenOptions={{ enable: true }} />
      {status === 'error' && (
        <View style={s.err} pointerEvents="none">
          <Text style={s.errText}>Video unavailable{error?.message ? `\n${error.message}` : ''}</Text>
        </View>
      )}
    </View>
  )
}

const s = StyleSheet.create({
  box: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#000' },
  err: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(0,0,0,0.6)' },
  errText: { color: C.text2, fontSize: 13, textAlign: 'center' },
})
