// Ported from frontend/src/games/ClipCard.tsx: poster that expands into an inline expo-video player.
// Poster and video share one 16:9 <MediaFrame> (letterboxed, never cropped); badges sit below it.
// Instagram-style: with an `autoplayKey` (its play id) the clip autoplays muted while its row is on
// screen and pauses when it scrolls away (lib/autoplay.ts); tapping the poster plays it with sound.
import { Image } from 'expo-image'
import { useEffect, useRef, useState } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'

import { useApi } from '@/lib/ApiContext'
import * as autoplay from '@/lib/autoplay'
import { C, F, SOURCE_COLORS } from '@/lib/theme'
import { absUrl } from '@/shared/apiBase'
import { formatDuration } from '@/shared/format'
import { alternateLabels, clipOptions, eventTime, sourceKindOf, sourceLabel } from '@/shared/source'
import type { ClipRef, SourceKind } from '@/shared/types'
import { fillFrame, MEDIA_MAX_WIDTH, MediaFrame } from './MediaFrame'
import { VideoPlayer } from './VideoPlayer'

export function SourcePill({ kind, league }: { kind: SourceKind; league?: string }) {
  const c = SOURCE_COLORS[kind]
  return <Text style={[s.source, { color: c.fg, backgroundColor: c.bg, borderColor: c.border }]}>{sourceLabel(kind, league)}</Text>
}

export function Poster({ uri, title, duration, disabled, onPress, label }: {
  uri: string | null; title: string; duration: string | null; disabled?: boolean; onPress: () => void; label?: string
}) {
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" accessibilityLabel={label ?? `Play clip: ${title}`}
      style={({ pressed }) => [s.poster, !uri && s.posterEmpty, pressed && { opacity: 0.85 }]}>
      {uri ? <Image source={{ uri }} style={fillFrame} contentFit="contain" transition={150} cachePolicy="memory-disk" />
        : <Text style={s.posterFallback} numberOfLines={3}>{title}</Text>}
      {!disabled && <View style={s.playBtn}><Text style={s.playIcon}>▶</Text></View>}
      {!!duration && <Text style={s.duration}>{duration}</Text>}
      {disabled && <Text style={s.unavailable}>Video unavailable</Text>}
    </Pressable>
  )
}

export function ClipCard({ clip, alternates, league, isNew, reason, compact, autoplayKey }: {
  clip: ClipRef; alternates?: ClipRef[] | null; league: string; isNew?: boolean; reason?: string | null; compact?: boolean
  /** Play id: lets the game screen's visibility tracking autoplay this clip. */
  autoplayKey?: string
}) {
  const { url: base } = useApi()
  const options = clipOptions(clip, alternates)
  const labels = alternateLabels(options, league)
  const [activeId, setActiveId] = useState(clip.event_id)
  const [playing, setPlaying] = useState(false)   // video mounted (poster swapped out)
  const [running, setRunning] = useState(false)   // should be playing right now
  const [firstFrame, setFirstFrame] = useState(false)
  const active = options.find(c => c.event_id === activeId) ?? clip
  const kind = sourceKindOf({ source_kind: active.source_kind })
  const video = absUrl(base, active.video_url)
  const poster = absUrl(base, active.poster_url)
  const showVideo = playing && !!video
  const keyRef = useRef(autoplayKey)
  keyRef.current = autoplayKey

  const frameRef = useRef<View>(null)

  useEffect(() => {
    if (!autoplayKey || !video) return
    // react-native-web: a View ref is its DOM node, so an IntersectionObserver can watch the frame.
    const el = autoplay.usesIntersectionObserver ? frameRef.current as unknown as HTMLElement | null : null
    const unregister = autoplay.register(autoplayKey, {
      play: () => { setPlaying(true); setRunning(true) },
      pause: () => setRunning(false),
      top: el ? () => el.getBoundingClientRect().top : undefined,
    })
    if (!el) return unregister
    const io = new IntersectionObserver(([e]) => autoplay.setRatio(autoplayKey, e.intersectionRatio),
      { threshold: [0, autoplay.ANY, autoplay.KEEP, 0.5, autoplay.START, 0.8, 1] })
    io.observe(el)
    return () => { io.disconnect(); unregister() }
  }, [autoplayKey, video])
  useEffect(() => { setFirstFrame(false) }, [video])

  const playNow = () => { autoplay.claim(keyRef.current, true); setPlaying(true); setRunning(true) }

  return (
    <View style={[s.card, isNew && s.cardNew]} testID={`clip-${active.event_id}`}>
      <MediaFrame testID="clip-media" ref={frameRef}>
        {showVideo
          ? <VideoPlayer key={video} url={video} active={running} onFirstFrame={() => setFirstFrame(true)}
              onEnd={() => autoplay.ended(keyRef.current)} onUserPlay={() => { autoplay.claim(keyRef.current); setRunning(true) }} />
          : <Poster uri={poster} title={active.title} duration={formatDuration(active.duration_seconds)} disabled={!video} onPress={playNow} />}
        {/* keep the thumbnail up until the video has a frame, so autoplay doesn't flash black */}
        {showVideo && !firstFrame && !!poster && (
          <View style={s.posterHold}><Image source={{ uri: poster }} style={fillFrame} contentFit="contain" cachePolicy="memory-disk" /></View>
        )}
        {isNew && !showVideo && <Text style={s.newFlash}>NEW CLIP</Text>}
      </MediaFrame>
      <View style={s.meta} testID="clip-meta">
        <SourcePill kind={kind} league={league} />
        {active.social_score != null && <Text style={s.social}>🔥 {active.social_score.toFixed(2)}</Text>}
        {compact && <Text style={s.title} numberOfLines={2}>{active.title}</Text>}
        {!compact && !!reason && <Text style={s.reason} numberOfLines={1}>{reason}</Text>}
      </View>
      {compact && !!active.occurred_utc && <Text style={[s.reason, { marginTop: 4 }]}>{eventTime(active.occurred_utc)}</Text>}
      {options.length > 1 && (
        <View style={s.alts} accessibilityLabel="Alternate clips">
          <Text style={s.reason}>Also:</Text>
          {options.map((c, i) => {
            const on = c.event_id === active.event_id
            return (
              <Pressable key={c.event_id} onPress={() => setActiveId(c.event_id)} accessibilityRole="button" accessibilityState={{ selected: on }}
                style={[s.alt, on && s.altOn]} hitSlop={4}>
                <Text style={[s.altText, on && { color: C.accent }]} numberOfLines={1}>{labels[i]}</Text>
              </Pressable>
            )
          })}
        </View>
      )}
    </View>
  )
}

const s = StyleSheet.create({
  // Full width up to MEDIA_MAX_WIDTH, then centered; the media frame is 100% of the card.
  card: { width: '100%', maxWidth: MEDIA_MAX_WIDTH, alignSelf: 'center', marginTop: 8, borderRadius: 10, backgroundColor: C.bg2, borderWidth: 1, borderColor: C.line, overflow: 'hidden' },
  cardNew: { borderColor: C.accent },
  poster: { ...fillFrame, alignItems: 'center', justifyContent: 'center', backgroundColor: '#000' },
  posterEmpty: { backgroundColor: C.panel2 },
  posterHold: { ...fillFrame, backgroundColor: '#000', pointerEvents: 'none' },
  posterFallback: { color: C.text2, padding: 16, textAlign: 'center', fontWeight: '600' },
  playBtn: { width: 52, height: 52, borderRadius: 26, backgroundColor: 'rgba(255,106,26,0.92)', alignItems: 'center', justifyContent: 'center' },
  playIcon: { color: C.white, fontSize: 22, marginLeft: 3 },
  duration: { position: 'absolute', right: 8, bottom: 8, overflow: 'hidden', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: 'rgba(0,0,0,0.7)', color: C.white, fontSize: 11, fontFamily: F.mono },
  unavailable: { position: 'absolute', left: 8, bottom: 8, color: C.text2, fontSize: 11, backgroundColor: 'rgba(0,0,0,0.7)', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, overflow: 'hidden' },
  newFlash: { position: 'absolute', top: 8, left: 8, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4, backgroundColor: C.accent, color: C.white, fontWeight: '800', fontSize: 11, letterSpacing: 1 },
  meta: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 8 },
  source: { overflow: 'hidden', fontWeight: '800', fontSize: 10, letterSpacing: 0.8, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, borderWidth: 1 },
  social: { color: C.warn, fontSize: 11, fontWeight: '700' },
  title: { color: C.text, fontSize: 13, fontWeight: '600', flexBasis: '100%' },
  reason: { color: C.muted, fontSize: 11, flexShrink: 1 },
  alts: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingBottom: 10 },
  alt: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, borderWidth: 1, borderColor: C.line2, maxWidth: 200 },
  altOn: { borderColor: C.accentBorder, backgroundColor: C.accentBg },
  altText: { color: C.text2, fontSize: 11, fontWeight: '600' },
})
