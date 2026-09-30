// The one 16:9 box every clip renders in. The poster and the playing video both fill it
// (absolutely positioned), so pressing play never changes its size, and resizing the window only
// scales it. Callers cap the width (MEDIA_MAX_WIDTH on cards) so it doesn't balloon on wide screens.
import type { ReactNode, Ref } from 'react'
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'

export const MEDIA_ASPECT = 16 / 9
/** Widest a clip card / library card gets; it's centered beyond that. */
export const MEDIA_MAX_WIDTH = 720

export function MediaFrame({ children, style, testID, ref }: {
  children?: ReactNode; style?: StyleProp<ViewStyle>; testID?: string; ref?: Ref<View>
}) {
  return <View ref={ref} style={[s.frame, style]} testID={testID}>{children}</View>
}

/** Fill the frame exactly. Explicit width/height matter on web: an absolutely positioned
 *  <video>/<img> with only inset: 0 keeps its intrinsic size instead of stretching. */
export const fillFrame = {
  position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, width: '100%', height: '100%',
} as const satisfies ViewStyle

const s = StyleSheet.create({
  frame: { width: '100%', aspectRatio: MEDIA_ASPECT, backgroundColor: '#000', overflow: 'hidden' },
})
