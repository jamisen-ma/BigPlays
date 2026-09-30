import { useEvent, useEventListener } from 'expo'
import { useVideoPlayer, VideoView } from 'expo-video'
import { useEffect, useRef } from 'react'
import { Platform, StyleSheet, Text, View } from 'react-native'

import { setSoundOn, shouldMute, userGesture } from '@/lib/autoplay'
import { C } from '@/lib/theme'
import { fillFrame } from './MediaFrame'

/**
 * expo-video player for one URL. It fills its parent, which should be a <MediaFrame> (the same
 * 16:9 box the poster used), and letterboxes (contentFit="contain") so nothing is ever cropped.
 * Mount it with `key={url}` so switching clips gets a fresh player.
 *
 * `active` drives play/pause (inline autoplay pauses it when it scrolls away; the full-screen
 * player just passes true). Muted unless the viewer turned sound on (see lib/autoplay.ts).
 * Plays inline; native controls, user-initiated fullscreen + PiP.
 */
export function VideoPlayer({ url, active = true, onEnd, onUserPlay, onFirstFrame }: {
  url: string; active?: boolean; onEnd?: () => void; onUserPlay?: () => void; onFirstFrame?: () => void
}) {
  const player = useVideoPlayer(url, p => { p.loop = false })
  const view = useRef<VideoView>(null)
  const selfPlayAt = useRef(0)
  const { status, error } = useEvent(player, 'statusChange', { status: player.status, error: undefined })

  // Runs after VideoView has mounted its <video> (child effects run first). On web, calling
  // player.play() before that is a no-op, which used to leave the clip paused after the first tap.
  useEffect(() => {
    if (!active) { player.pause(); return }
    player.muted = shouldMute()
    selfPlayAt.current = Date.now()
    if (Platform.OS === 'web') {
      // Play the <video> directly so a blocked/interrupted play() is caught (expo-video's web
      // play() leaves the promise unhandled); fall back to muted if sound isn't allowed yet.
      const el = (view.current as unknown as { nativeRef?: { current?: HTMLVideoElement } } | null)?.nativeRef?.current
      if (el) {
        if (el.ended) return
        el.play().catch(() => { if (!el.muted) { el.muted = true; player.muted = true; el.play().catch(() => {}) } })
        return
      }
    }
    player.play()
  }, [active, player])

  // onFirstFrameRender can be missed on web if the <video> loads before VideoView's listener is
  // attached; "ready to play" means a frame is there too.
  useEffect(() => { if (status === 'readyToPlay') onFirstFrame?.() }, [status, onFirstFrame])
  useEventListener(player, 'playToEnd', () => onEnd?.())
  useEventListener(player, 'playingChange', ({ isPlaying }) => {
    // The viewer pressed ▶ on the native controls (not our own play() above).
    const ours = Date.now() - selfPlayAt.current < 1500
    if (isPlaying && !ours && (Platform.OS !== 'web' || userGesture())) onUserPlay?.()
  })
  useEventListener(player, 'mutedChange', ({ muted }) => {
    // Remember the viewer's sound choice for the next autoplaying clip.
    if (Platform.OS !== 'web' || userGesture()) setSoundOn(!muted)
  })

  return (
    <View style={s.fill}>
      {/* On web VideoView is a bare <video> that gets this style verbatim: it needs explicit
          width/height 100% (inset alone leaves it at the video's intrinsic size, e.g. 1280x720). */}
      <VideoView ref={view} player={player} style={s.video} nativeControls contentFit="contain" playsInline
        allowsPictureInPicture fullscreenOptions={{ enable: true }} onFirstFrameRender={onFirstFrame} />
      {status === 'error' && (
        <View style={s.err} pointerEvents="none">
          <Text style={s.errText}>Video unavailable{error?.message ? `\n${error.message}` : ''}</Text>
        </View>
      )}
    </View>
  )
}

const s = StyleSheet.create({
  fill: { ...fillFrame, backgroundColor: '#000' },
  video: { ...fillFrame, backgroundColor: '#000' },
  err: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(0,0,0,0.6)' },
  errText: { color: C.text2, fontSize: 13, textAlign: 'center' },
})
